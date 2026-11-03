"""Bouncer, r2's AutoModerator: a community's YAML rules, checked against each new post.

A subset of r2's rule language with its syntax and meanings. Rules are YAML documents separated by
``---``. Each may set:

- ``type``: any (default), submission, text submission, link submission, comment;
- match fields, ``title``/``body``/``domain``/``url``/``id``/``flair_text``/``flair_css_class``
  (comments: ``body``/``id``), joined with ``+`` to search several, prefixed with ``~`` to negate,
  with modifiers in parentheses: includes-word (the default), includes, full-exact, full-text,
  starts-with, ends-with, regex, case-sensitive;
- checks: ``body_longer_than``, ``body_shorter_than``, ``is_top_level``, ``reports``;
- ``author``: ``name`` matches and ``comment_karma``/``post_karma``/``combined_karma``/
  ``account_age`` comparisons such as ``< 10`` or ``< 7 days``;
- actions: ``action`` (approve, remove, spam, filter, report) with ``action_reason``,
  ``set_locked``, ``set_nsfw``, ``set_sticky``, ``comment``, ``modmail`` (+ ``modmail_subject``);
- ``priority`` (higher first) and ``moderators_exempt``.

Placeholders ``{{author}}``, ``{{title}}``, ``{{body}}``, ``{{permalink}}``, ``{{community}}``,
``{{domain}}`` and ``{{match}}`` fill in comment and modmail text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import yaml

from app.errors import InaError
from app.repos.base import ModPermission, NameTakenError, NewMessage
from app.services import comments, moderation
from app.services.accounts import BOUNCER
from app.services.messaging import MAX_SUBJECT
from app.services.perms import Standing
from app.text import markdown

if TYPE_CHECKING:
    from app.repos.base import (
        Account,
        AccountRepo,
        Comment,
        CommentRepo,
        Community,
        CommunityRepo,
        Link,
        MessageRepo,
        ModerationRepo,
        VoteRepo,
    )

TYPES = {
    'any': {'link', 'comment'},
    'submission': {'link'},
    'link submission': {'link'},
    'text submission': {'link'},
    'comment': {'comment'},
}
LINK_FIELDS = {'title', 'body', 'domain', 'url', 'id', 'flair_text', 'flair_css_class'}
COMMENT_FIELDS = {'body', 'id'}
AUTHOR_FIELDS = {'name', 'id'}
# The value is the first group, so ``{{match}}`` leaves out the word boundaries around it.
MATCHES = {
    'full-exact': '^({})$',
    'full-text': r'^\W*({})\W*$',
    'includes': '({})',
    'includes-word': r'(?:^|\W|\b)({})(?:$|\W|\b)',
    'starts-with': '^({})',
    'ends-with': '({})$',
}
MODIFIERS = set(MATCHES) | {'case-sensitive', 'regex'}
# r2 defaulted these fields to something other than includes-word.
FIELD_DEFAULTS = {
    'id': 'full-exact',
    'flair_text': 'full-exact',
    'flair_css_class': 'full-exact',
    'url': 'includes',
    'name': 'full-exact',
}
ACTIONS = {'approve', 'remove', 'spam', 'filter', 'report'}
REMOVALS = {'remove', 'spam', 'filter'}
CHECKS = {'body_longer_than', 'body_shorter_than', 'is_top_level', 'reports'}
AUTHOR_CHECKS = {'comment_karma', 'post_karma', 'link_karma', 'combined_karma', 'account_age'}
RULE_KEYS = (
    {'type', 'priority', 'moderators_exempt', 'author'}
    | CHECKS
    | {'action', 'action_reason', 'report_reason', 'set_locked', 'set_nsfw', 'set_sticky'}
    | {'comment', 'comment_stickied', 'modmail', 'modmail_subject'}
)
KEY = re.compile(r'^(?P<negate>~)?(?P<fields>[a-z_]+(?:\+[a-z_]+)*)\s*(?:\((?P<mods>[^)]*)\))?$')
COMPARISON = re.compile(r'^(?P<op>==?|<|>)?\s*(?P<value>.+)$')
PERIOD = re.compile(r'^(?P<count>\d+)\s*(?P<unit>minute|hour|day|week|month|year)?s?$')
PERIODS = {
    'minute': timedelta(minutes=1),
    'hour': timedelta(hours=1),
    'day': timedelta(days=1),
    'week': timedelta(weeks=1),
    'month': timedelta(days=30),
    'year': timedelta(days=365),
}
# Bouncer acts as a moderator with every permission.
BOT_STANDING = Standing(mod=ModPermission.ALL)
PLACEHOLDER = re.compile(r'\{\{\s*(author|title|body|permalink|community|domain|match)\s*\}\}')


class RuleError(Exception):
    """A rule that cannot be used, with what is wrong, for the moderator editing it."""


@dataclass(frozen=True, slots=True)
class Match:
    fields: tuple[str, ...]
    values: tuple[str, ...]
    negate: bool
    how: str
    case_sensitive: bool
    regex: bool

    def search(self, texts: dict[str, str]) -> str | None:
        """The text that matched (for ``{{match}}``), '' for a negated pass, None for no match."""
        flags = 0 if self.case_sensitive else re.IGNORECASE
        found = None
        for value in self.values:
            pattern = MATCHES[self.how].format(value if self.regex else re.escape(value))
            for name in self.fields:
                if hit := re.search(pattern, texts.get(name, ''), flags):
                    found = hit.group(1)
                    break
            if found is not None:
                break
        if self.negate:
            return '' if found is None else None
        return found


@dataclass(frozen=True, slots=True)
class Comparison:
    op: str
    value: int | timedelta

    def holds(self, actual: int | timedelta) -> bool:
        if self.op == '<':
            return actual < self.value  # type: ignore[operator]
        if self.op == '>':
            return actual > self.value  # type: ignore[operator]
        return actual == self.value


@dataclass(frozen=True, slots=True, kw_only=True)
class Rule:
    kinds: set[str]
    text_only: bool | None
    priority: int
    matches: tuple[Match, ...]
    author_matches: tuple[Match, ...]
    # is_top_level is a bool; the other checks are counts.
    checks: dict[str, int] = field(default_factory=dict)
    author_checks: dict[str, Comparison] = field(default_factory=dict)
    action: str | None = None
    action_reason: str = ''
    set_locked: bool | None = None
    set_nsfw: bool | None = None
    set_sticky: bool | None = None
    comment: str | None = None
    comment_stickied: bool = False
    modmail: str | None = None
    modmail_subject: str = 'from Bouncer'
    moderators_exempt: bool = True


def _strings(value: object, where: str) -> tuple[str, ...]:
    values = value if isinstance(value, list) else [value]
    if not values or not all(isinstance(v, str | int) for v in values):
        msg = f'{where}: expected text or a list of text'
        raise RuleError(msg)
    return tuple(str(v) for v in values)


def _match(key: str, value: object, allowed: set[str]) -> Match:
    parsed = KEY.match(key.strip())
    if parsed is None:
        msg = f'cannot read the key "{key}"'
        raise RuleError(msg)
    fields = tuple(parsed['fields'].split('+'))
    if unknown := [name for name in fields if name not in allowed]:
        msg = f'"{unknown[0]}" is not a field that can be matched here'
        raise RuleError(msg)
    modifiers = {m.strip() for m in (parsed['mods'] or '').split(',') if m.strip()}
    if bad := modifiers - MODIFIERS:
        msg = f'unknown modifier "{min(bad)}" on "{key}"'
        raise RuleError(msg)
    how = next(
        (m for m in modifiers if m in MATCHES), FIELD_DEFAULTS.get(fields[0], 'includes-word')
    )
    values = _strings(value, key)
    if 'regex' in modifiers:
        for pattern in values:
            try:
                re.compile(pattern)
            except re.error as error:
                msg = f'bad regex in "{key}": {error}'
                raise RuleError(msg) from None
    return Match(
        fields=fields,
        values=values,
        negate=bool(parsed['negate']),
        how=how,
        case_sensitive='case-sensitive' in modifiers,
        regex='regex' in modifiers,
    )


def _comparison(key: str, value: object) -> Comparison:
    parsed = COMPARISON.match(str(value).strip())
    if parsed is None:
        msg = f'cannot read the comparison "{key}: {value}"'
        raise RuleError(msg)
    op = '=' if parsed['op'] in {None, '=', '=='} else parsed['op']
    text = parsed['value'].strip()
    if key == 'account_age':
        period = PERIOD.match(text)
        if period is None:
            msg = f'cannot read the period "{text}"'
            raise RuleError(msg)
        unit = PERIODS[period['unit'] or 'day']
        return Comparison(op, unit * int(period['count']))
    try:
        return Comparison(op, int(text))
    except ValueError:
        msg = f'"{key}" needs a number'
        raise RuleError(msg) from None


def _check(name: str, value: object) -> int:
    if name == 'is_top_level':
        if not isinstance(value, bool):
            msg = 'is_top_level: expected true or false'
            raise RuleError(msg)
        return value
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = f'{name}: expected a number'
        raise RuleError(msg)
    return value


def _author(value: object) -> tuple[tuple[Match, ...], dict[str, Comparison]]:
    if not isinstance(value, dict):
        msg = 'author: expected a set of checks'
        raise RuleError(msg)
    matches, checks = [], {}
    for key, inner in value.items():
        name = str(key).strip()
        if name in AUTHOR_CHECKS:
            checks['post_karma' if name == 'link_karma' else name] = _comparison(name, inner)
        else:
            matches.append(_match(name, inner, AUTHOR_FIELDS))
    return tuple(matches), checks


def _rule(document: dict[str, Any]) -> Rule:
    kind = str(document.get('type', 'any'))
    if kind not in TYPES:
        msg = f'unknown type "{kind}"'
        raise RuleError(msg)
    allowed = LINK_FIELDS if TYPES[kind] == {'link'} else COMMENT_FIELDS
    if kind == 'any':
        allowed = LINK_FIELDS | COMMENT_FIELDS
    matches, checks = [], {}
    for key, value in document.items():
        name = str(key)
        if name in CHECKS:
            checks[name] = _check(name, value)
        elif name not in RULE_KEYS:
            matches.append(_match(name, value, allowed))
    action = document.get('action')
    if action is not None and action not in ACTIONS:
        msg = f'unknown action "{action}"'
        raise RuleError(msg)
    author_matches, author_checks = _author(document.get('author', {}))
    exempt = document.get('moderators_exempt')
    return Rule(
        kinds=TYPES[kind],
        text_only={'text submission': True, 'link submission': False}.get(kind),
        priority=int(document.get('priority', 0)),
        matches=tuple(matches),
        author_matches=author_matches,
        checks=checks,
        author_checks=author_checks,
        action=action,
        action_reason=str(document.get('action_reason', document.get('report_reason', ''))),
        set_locked=document.get('set_locked'),
        set_nsfw=document.get('set_nsfw'),
        set_sticky=bool(document['set_sticky']) if 'set_sticky' in document else None,
        comment=document.get('comment'),
        comment_stickied=bool(document.get('comment_stickied', False)),
        modmail=document.get('modmail'),
        modmail_subject=str(document.get('modmail_subject', 'from Bouncer')),
        moderators_exempt=bool(exempt) if exempt is not None else action in REMOVALS | {'report'},
    )


def parse(text: str) -> list[Rule]:
    """The rules, highest priority first; RuleError names the rule that is wrong."""
    try:
        documents = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as error:
        msg = f'the rules are not valid YAML: {error}'
        raise RuleError(msg) from None
    rules = []
    for number, document in enumerate(documents, start=1):
        if not isinstance(document, dict):
            msg = f'rule {number}: expected a set of keys and values'
            raise RuleError(msg)
        try:
            rules.append(_rule(document))
        except RuleError as error:
            msg = f'rule {number}: {error}'
            raise RuleError(msg) from None
    return sorted(rules, key=lambda rule: rule.priority, reverse=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class Item:
    """What a rule looks at: a new link, or a new comment and its link."""

    link: Link
    comment: Comment | None
    author: Account
    author_is_moderator: bool
    permalink: str

    @property
    def kind(self) -> str:
        return 'comment' if self.comment else 'link'

    def texts(self) -> dict[str, str]:
        if self.comment is not None:
            return {'body': self.comment.body_md, 'id': self.comment.fullname.id36}
        link = self.link
        return {
            'title': link.title,
            'body': link.selftext_md,
            'domain': link.domain,
            'url': link.url,
            'id': link.fullname.id36,
            'flair_text': link.flair_text or '',
            'flair_css_class': link.flair_css or '',
        }

    @property
    def body(self) -> str:
        return self.comment.body_md if self.comment else self.link.selftext_md


def _author_holds(rule: Rule, item: Item, now: datetime) -> bool:
    author = item.author
    values: dict[str, int | timedelta] = {
        'comment_karma': author.comment_karma,
        'post_karma': author.link_karma,
        'combined_karma': author.comment_karma + author.link_karma,
        'account_age': now - author.created,
    }
    return all(check.holds(values[name]) for name, check in rule.author_checks.items())


def _checks_hold(rule: Rule, item: Item) -> bool:
    checks, length = rule.checks, len(item.body)
    top_level = item.comment is None or item.comment.parent_id is None
    return (
        length > checks.get('body_longer_than', -1)
        and length < checks.get('body_shorter_than', length + 1)
        and (item.comment is None or checks.get('is_top_level', top_level) == top_level)
        and (item.comment or item.link).reported >= checks.get('reports', 0)
    )


def matched(rule: Rule, item: Item, now: datetime) -> str | None:
    """None if the rule does not apply; otherwise the text that matched, for ``{{match}}``."""
    applies = (
        item.kind in rule.kinds
        and (rule.text_only is None or item.link.is_self == rule.text_only)
        and not (rule.moderators_exempt and item.author_is_moderator)
        and _checks_hold(rule, item)
        and _author_holds(rule, item, now)
    )
    if not applies:
        return None
    texts = item.texts()
    found = ''
    for match in rule.matches:
        hit = match.search(texts)
        if hit is None:
            return None
        found = found or hit
    author_texts = {'name': item.author.name, 'id': item.author.fullname.id36}
    if any(match.search(author_texts) is None for match in rule.author_matches):
        return None
    return found


def fill(text: str, item: Item, community: str, found: str) -> str:
    values = {
        'author': item.author.name,
        'title': item.link.title,
        'body': item.body,
        'permalink': item.permalink,
        'community': community,
        'domain': item.link.domain,
        'match': found,
    }
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


@dataclass(frozen=True, slots=True)
class Repos:
    accounts: AccountRepo
    communities: CommunityRepo
    moderation: ModerationRepo
    comments: CommentRepo
    votes: VoteRepo
    messages: MessageRepo


@dataclass(slots=True)
class Outcome:
    """What the rules did, for the caller to follow up once committed."""

    action: str | None = None
    changed: bool = False
    comments: list[Comment] = field(default_factory=list)


async def system_account(accounts: AccountRepo) -> Account:
    """The account Bouncer acts as; nobody can sign in to it or register its name."""
    if (account := await accounts.by_name(BOUNCER)) is not None:
        return account
    try:
        # No argon2 hash verifies against this, so the account cannot sign in.
        return await accounts.create(name=BOUNCER, password_hash='!', email=None)  # noqa: S106
    except NameTakenError:
        account = await accounts.by_name(BOUNCER)
        assert account is not None  # noqa: S101 - created by whoever won the race
        return account


async def moderate(
    repos: Repos, rules: list[Rule], item: Item, community: Community, *, now: datetime
) -> Outcome:
    """Apply every rule that matches ``item``; only the first matching rule's action is taken."""
    outcome = Outcome()
    if item.author.name == BOUNCER:
        return outcome
    bot = None
    for rule in rules:
        found = matched(rule, item, now)
        if found is None:
            continue
        bot = bot or await system_account(repos.accounts)
        text = Filler(item, community.name, found)
        if item.comment is None:
            await _set_flags(repos, rule, item.link, community, bot, outcome)
        if rule.comment is not None:
            await _reply(repos, rule, item, community, bot, text(rule.comment), outcome, now=now)
        if rule.modmail is not None:
            await _modmail(repos, community, bot, text(rule.modmail_subject), text(rule.modmail))
        if rule.action is not None and outcome.action is None:
            outcome.action, outcome.changed = rule.action, True
            await _act(repos, rule.action, item, community, bot, text(rule.action_reason))
    return outcome


@dataclass(frozen=True, slots=True)
class Filler:
    item: Item
    community: str
    found: str

    def __call__(self, text: str) -> str:
        return fill(text, self.item, self.community, self.found)


async def _set_flags(
    repos: Repos, rule: Rule, link: Link, community: Community, bot: Account, outcome: Outcome
) -> None:
    if rule.set_locked is not None:
        await moderation.lock(
            repos.moderation, bot, BOT_STANDING, community, link, locked=rule.set_locked
        )
    if rule.set_nsfw is not None:
        await repos.moderation.set_link_flags(link.id, nsfw=rule.set_nsfw)
        action = 'marknsfw' if rule.set_nsfw else 'unmarknsfw'
        await repos.moderation.log(community.id, bot.id, action, link.fullname)
    if rule.set_sticky is not None:
        await moderation.sticky(
            repos.moderation,
            repos.communities,
            bot,
            BOT_STANDING,
            community,
            link,
            state=rule.set_sticky,
        )
    flags = (rule.set_locked, rule.set_nsfw, rule.set_sticky)
    outcome.changed = outcome.changed or any(flag is not None for flag in flags)


async def _reply(
    repos: Repos,
    rule: Rule,
    item: Item,
    community: Community,
    bot: Account,
    text: str,
    outcome: Outcome,
    *,
    now: datetime,
) -> None:
    """Reply to the item, distinguished, as r2's AutoModerator did."""
    try:
        reply = await comments.post(
            repos.comments,
            repos.votes,
            bot,
            item.link,
            community,
            item.comment,
            text,
            standing=BOT_STANDING,
            ip='',
            now=now,
        )
    except InaError:
        # Too long once filled in, say; the rest of the rule still applies.
        return
    await repos.moderation.set_distinguished(reply.fullname, 'moderator')
    if rule.comment_stickied and item.comment is None:
        await repos.moderation.set_sticky_comment(item.link.id, reply.id)
    outcome.comments.append(reply)
    outcome.changed = True


async def _modmail(
    repos: Repos, community: Community, bot: Account, subject: str, text: str
) -> None:
    await repos.messages.create(
        NewMessage(
            from_id=bot.id,
            to_id=None,
            community_id=community.id,
            parent=None,
            subject=subject[:MAX_SUBJECT],
            body_md=text,
            body_html=markdown.render(text),
            from_community=False,
            ip='',
            created=datetime.now(UTC),
        )
    )


async def _act(
    repos: Repos, action: str, item: Item, community: Community, bot: Account, reason: str
) -> None:
    thing = (item.comment or item.link).fullname
    if action == 'report':
        await repos.moderation.report(thing, bot.id, reason[: moderation.MAX_REASON])
    elif action == 'approve':
        await moderation.approve(repos.moderation, bot, BOT_STANDING, community, thing)
    else:
        # A filtered item waits in the modqueue; removed and spammed ones do not.
        await moderation.remove(
            repos.moderation,
            bot,
            BOT_STANDING,
            community,
            thing,
            spam=action == 'spam',
            removed_by='filter' if action == 'filter' else 'mod',
            details=reason,
        )
