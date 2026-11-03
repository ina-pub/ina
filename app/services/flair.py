"""Flair, as r2 had it: a user's text and CSS classes in one community, and a link's.

Moderators with the 'flair' permission set anyone's flair and keep the templates people choose
from. Where the community allows it, users pick their own flair, and submitters their link's.
Moderators' changes to someone else's flair go to the modlog as r2's ``editflair``.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

import msgspec

from app.errors import ErrorCode, InaError
from app.repos.base import FlairKind, ModPermission, TooManyTemplatesError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.repos.base import (
        Account,
        AccountRepo,
        Community,
        CommunityRepo,
        FlairRepo,
        FlairTemplate,
        Link,
        ModerationRepo,
        Prefs,
        UserFlair,
    )
    from app.services.perms import Standing

FORBIDDEN = 403
NOT_FOUND = 404
MAX_TEXT = 64
MAX_CLASSES = 10
MAX_CSV_ROWS = 100
CSS_NAME = re.compile(r'\A[a-zA-Z0-9\-]{1,100}\Z')
POSITIONS = ('left', 'right')
LINK_POSITIONS = ('', 'left', 'right')


def check_text(text: str, field_name: str = 'text') -> str:
    if len(text) > MAX_TEXT:
        raise InaError.of(ErrorCode.TOO_LONG, field_name, max_length=MAX_TEXT)
    return text.strip()


def check_css(css: str, field_name: str = 'css_class') -> str:
    """r2's flair classes: up to ten names of letters, digits and dashes."""
    names = css.split()
    if len(names) > MAX_CLASSES:
        raise InaError.of(ErrorCode.TOO_MUCH_FLAIR_CSS, field_name)
    if not all(CSS_NAME.match(name) for name in names):
        raise InaError.of(ErrorCode.BAD_CSS_NAME, field_name)
    return ' '.join(names)


def may_moderate(standing: Standing) -> bool:
    return standing.has(ModPermission.FLAIR)


def require_moderator(standing: Standing) -> None:
    if not may_moderate(standing):
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)


def can_flair_user(
    actor: Account, target_id: int, community: Community, standing: Standing
) -> bool:
    """r2's can_flair_in_sr: oneself where self-assignment is on, or anyone as a moderator."""
    own = actor.id == target_id and community.settings.flair_self_assign_enabled
    return own or may_moderate(standing)


def can_flair_link(actor: Account, link: Link, community: Community, standing: Standing) -> bool:
    """r2's can_flair_slow: a moderator, or the submitter where self-assignment is on."""
    own = link.author_id == actor.id and community.settings.link_flair_self_assign_enabled
    return own or may_moderate(standing)


async def set_user_flair(
    flair: FlairRepo,
    moderation: ModerationRepo,
    actor: Account,
    community: Community,
    target: Account,
    *,
    text: str,
    css_class: str,
    details: str = 'edit',
) -> None:
    """Empty text and classes remove the flair, as on r2."""
    if not text and not css_class:
        await flair.set_user_flair(community.id, target.id, text=None, css_class=None)
        details = 'delete'
    else:
        await flair.set_user_flair(
            community.id, target.id, text=text or None, css_class=css_class or None
        )
    if actor.id != target.id:
        await moderation.log(
            community.id, actor.id, 'editflair', target.fullname, f'flair_{details}'
        )


async def set_link_flair(
    flair: FlairRepo,
    moderation: ModerationRepo,
    actor: Account,
    community: Community,
    link: Link,
    *,
    text: str,
    css_class: str,
) -> None:
    await flair.set_link_flair(link.id, text=text or None, css_class=css_class or None)
    if actor.id != link.author_id:
        await moderation.log(community.id, actor.id, 'editflair', link.fullname, 'flair_edit')


@dataclass
class ConfigForm:
    """``/api/flairconfig``, with r2's field names."""

    flair_enabled: bool = False
    flair_position: str = 'right'
    link_flair_position: str = ''
    flair_self_assign_enabled: bool = False
    link_flair_self_assign_enabled: bool = False


# Each setting, and how r2 named its change in the modlog.
CONFIG_DETAILS = {
    'flair_enabled': 'flair_enabled',
    'flair_position': 'flair_position',
    'link_flair_position': 'link_flair_position',
    'flair_self_assign_enabled': 'flair_self_enabled',
    'link_flair_self_assign_enabled': 'link_flair_self_enabled',
}


async def configure(
    communities: CommunityRepo,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    form: ConfigForm,
) -> None:
    require_moderator(standing)
    if form.flair_position not in POSITIONS:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'flair_position')
    if form.link_flair_position not in LINK_POSITIONS:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'link_flair_position')
    changes = {
        name: getattr(form, name)
        for name in CONFIG_DETAILS
        if getattr(form, name) != getattr(community.settings, name)
    }
    if not changes:
        return
    await communities.set_settings(
        community.id, msgspec.structs.replace(community.settings, **changes)
    )
    for name in changes:
        await moderation.log(community.id, actor.id, 'editflair', details=CONFIG_DETAILS[name])


def template_id(value: str) -> UUID | None:
    if not value.strip():
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'flair_template_id') from None


def flair_kind(value: str) -> FlairKind:
    try:
        return FlairKind(value or FlairKind.USER)
    except ValueError:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'flair_type') from None


async def save_template(
    flair: FlairRepo,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    *,
    kind: str,
    template: str,
    text: str,
    css_class: str,
    text_editable: bool,
) -> FlairTemplate:
    require_moderator(standing)
    try:
        saved = await flair.save_template(
            community.id,
            flair_kind(kind),
            template_id=template_id(template),
            text=check_text(text),
            css_class=check_css(css_class),
            text_editable=text_editable,
        )
    except TooManyTemplatesError:
        raise InaError.of(ErrorCode.TOO_MANY_FLAIR_TEMPLATES, 'flair_template_id') from None
    await moderation.log(community.id, actor.id, 'editflair', details='flair_template')
    return saved


async def delete_template(
    flair: FlairRepo,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    template: str,
) -> None:
    require_moderator(standing)
    wanted = template_id(template)
    if wanted is None or not await flair.delete_template(community.id, wanted):
        raise InaError.of(ErrorCode.NOT_FOUND, 'flair_template_id', status_code=NOT_FOUND)
    await moderation.log(community.id, actor.id, 'editflair', details='flair_delete_template')


async def clear_templates(
    flair: FlairRepo,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    kind: str,
) -> None:
    require_moderator(standing)
    await flair.clear_templates(community.id, flair_kind(kind))
    await moderation.log(community.id, actor.id, 'editflair', details='flair_clear_template')


async def select(
    flair: FlairRepo,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    *,
    link: Link | None,
    target: Account | None,
    template: str,
    text: str,
) -> None:
    """r2's selectflair: apply a template, with text where it may be edited; none removes."""
    if link is not None:
        kind, allowed = FlairKind.LINK, can_flair_link(actor, link, community, standing)
    elif target is not None:
        kind, allowed = FlairKind.USER, can_flair_user(actor, target.id, community, standing)
    else:
        raise InaError.of(ErrorCode.BAD_FLAIR_TARGET, 'name', status_code=NOT_FOUND)
    if not allowed:
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)
    wanted = template_id(template)
    chosen_text, css = '', ''
    if wanted is not None:
        chosen = await flair.template(community.id, wanted)
        if chosen is None or chosen.kind is not kind:
            raise InaError.of(ErrorCode.INVALID_OPTION, 'flair_template_id')
        # Text a user may not edit is the template's, as on r2.
        editable = chosen.text_editable or may_moderate(standing)
        chosen_text = (check_text(text) if editable else '') or chosen.text
        css = chosen.css_class
    if link is not None:
        await set_link_flair(
            flair, moderation, actor, community, link, text=chosen_text, css_class=css
        )
    elif target is not None:
        await set_user_flair(
            flair, moderation, actor, community, target, text=chosen_text, css_class=css
        )


@dataclass
class CsvLine:
    """One row's outcome, as r2's flaircsv reported it."""

    ok: bool = False
    status: str = ''
    warnings: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


async def apply_csv(
    flair: FlairRepo,
    moderation: ModerationRepo,
    accounts: AccountRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    text: str,
) -> list[CsvLine]:
    """Up to 100 ``user,text,css_class`` rows; empty text and class remove the flair."""
    require_moderator(standing)
    results = []
    for number, row in enumerate(csv.reader(text.strip().splitlines()), start=1):
        line = CsvLine()
        results.append(line)
        if number > MAX_CSV_ROWS:
            line.errors['row'] = f'stopped: {MAX_CSV_ROWS} rows at a time'
            break
        if len(row) != 3:  # noqa: PLR2004 - user, text, css class
            line.errors['row'] = 'skipped: a row needs a user, flair text and css class'
            continue
        name, flair_text, css_class = row
        target = await accounts.by_name(name.strip())
        if target is None:
            line.errors['user'] = f'skipped: no account is named {name}'
            continue
        if len(flair_text) > MAX_TEXT:
            flair_text = flair_text[:MAX_TEXT]
            line.warnings['text'] = f'flair text cut to {MAX_TEXT} characters'
        try:
            css_class = check_css(css_class)
        except InaError:
            line.errors['css'] = f"invalid css class `{css_class}', ignoring"
            continue
        await set_user_flair(
            flair,
            moderation,
            actor,
            community,
            target,
            text=flair_text.strip(),
            css_class=css_class,
            details='csv',
        )
        mode = 'added' if flair_text.strip() or css_class else 'removed'
        line.ok, line.status = True, f'{mode} flair for user {target.name}'
    return results


@dataclass(frozen=True, slots=True)
class Shown:
    """Flair as a page draws it."""

    text: str
    css_class: str
    # left or right of the name or title.
    position: str

    def classes(self, prefix: str) -> str:
        """r2 prefixed flair classes: flair-<name> for users, linkflair-<name> for links."""
        return ' '.join(f'{prefix}{name}' for name in self.css_class.split())


@dataclass(frozen=True, slots=True)
class FlairDisplay:
    """Which flair a page shows, by the communities' settings and the viewer's preferences."""

    users: dict[tuple[int, int], UserFlair] = field(default_factory=dict)
    communities: dict[int, Community] = field(default_factory=dict)
    show_user: bool = True
    show_link: bool = True

    def author(self, community_id: int, account_id: int) -> Shown | None:
        community = self.communities.get(community_id)
        found = self.users.get((community_id, account_id))
        if not self.show_user or community is None or not community.settings.flair_enabled:
            return None
        if found is None or not found.enabled or not (found.text or found.css_class):
            return None
        return Shown(found.text or '', found.css_class or '', community.settings.flair_position)

    def link(self, link: Link) -> Shown | None:
        community = self.communities.get(link.community_id)
        if not self.show_link or community is None or not (link.flair_text or link.flair_css):
            return None
        return Shown(
            link.flair_text or '', link.flair_css or '', community.settings.link_flair_position
        )


NO_FLAIR = FlairDisplay(show_user=False, show_link=False)


async def display(
    flair: FlairRepo,
    communities: CommunityRepo,
    prefs: Prefs,
    authors: list[tuple[int, int]],
    *,
    known: Sequence[Community] = (),
) -> FlairDisplay:
    """The flair for these (community id, author id) pairs; ``known`` saves loading those."""
    have = {community.id: community for community in known}
    missing = sorted({community_id for community_id, _ in authors} - set(have))
    have |= await communities.by_ids(missing)
    return FlairDisplay(
        users=await flair.user_flairs(authors) if prefs.show_flair else {},
        communities=have,
        show_user=prefs.show_flair,
        show_link=prefs.show_link_flair,
    )
