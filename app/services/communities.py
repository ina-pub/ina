"""Creating, configuring and subscribing to communities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import msgspec

from app.errors import ErrorCode, FieldError, InaError
from app.repos.base import CommunitySettings, CommunityText, ModPermission, NameTakenError
from app.services.perms import Action, Standing, can
from app.services.prefs import COMMENT_SORTS
from app.text import markdown

if TYPE_CHECKING:
    from app.repos.base import Account, Community, CommunityRepo, SubscriptionRepo
    from app.settings import Settings

NAME = re.compile(r'\A[A-Za-z0-9][A-Za-z0-9_]{2,20}\Z')
# Names r2 used for listings that are not communities.
RESERVED = frozenset(
    {'all', 'contrib', 'friends', 'mod', 'multi', 'myrandom', 'popular', 'randnsfw', 'random'}
)
TYPES = ('public', 'restricted', 'private')
LINK_TYPES = ('any', 'link', 'self')
SPAM_LEVELS = ('low', 'high', 'all')
WIKI_MODES = ('disabled', 'modonly', 'anyone')
MAX_TITLE = 100
MAX_PUBLIC_DESCRIPTION = 500
MAX_SIDEBAR = 5120
MAX_SUBMIT_TEXT = 1024
MAX_LABEL = 60
MAX_SCORE_HIDE_MINS = 1440


def _checked(value: str) -> bool:
    return value not in {'', 'off', 'false'}


@dataclass
class CommunityForm:
    """The settings form, with r2's field names. Every field is sent on every save, as on r2."""

    name: str = ''
    title: str = ''
    public_description: str = ''
    description: str = ''
    submit_text: str = ''
    submit_link_label: str = ''
    submit_text_label: str = ''
    type: str = 'public'
    link_type: str = 'any'
    over_18: str = ''
    show_media: str = ''
    show_media_preview: str = 'on'
    allow_top: str = 'on'
    collapse_deleted_comments: str = ''
    exclude_banned_modqueue: str = ''
    public_traffic: str = ''
    spam_links: str = 'high'
    spam_selfposts: str = 'high'
    spam_comments: str = 'low'
    comment_score_hide_mins: str = '0'
    suggested_comment_sort: str = ''
    wikimode: str = 'disabled'
    wiki_edit_karma: str = '100'
    wiki_edit_age: str = '0'

    @classmethod
    def of(cls, community: Community) -> CommunityForm:
        """The form as it stands for an existing community."""
        settings = community.settings

        def box(value: bool) -> str:  # noqa: FBT001
            return 'on' if value else ''

        return cls(
            name=community.name,
            title=community.title,
            public_description=community.description_md,
            description=community.sidebar_md,
            submit_text=settings.submit_text,
            submit_link_label=settings.submit_link_label,
            submit_text_label=settings.submit_text_label,
            type=community.type,
            link_type=settings.link_type,
            over_18=box(community.nsfw),
            show_media=box(settings.show_media),
            show_media_preview=box(settings.show_media_preview),
            allow_top=box(settings.allow_top),
            collapse_deleted_comments=box(settings.collapse_deleted_comments),
            exclude_banned_modqueue=box(settings.exclude_banned_modqueue),
            public_traffic=box(settings.public_traffic),
            spam_links=settings.spam_links,
            spam_selfposts=settings.spam_selfposts,
            spam_comments=settings.spam_comments,
            comment_score_hide_mins=str(settings.comment_score_hide_mins),
            suggested_comment_sort=settings.suggested_comment_sort or '',
            wikimode=settings.wikimode,
            wiki_edit_karma=str(settings.wiki_edit_karma),
            wiki_edit_age=str(settings.wiki_edit_age),
        )


class _Errors:
    """Collects every field's error so the form can show them all at once."""

    def __init__(self) -> None:
        self.found: list[FieldError] = []

    def add(self, code: ErrorCode, field: str, **params: str | int) -> None:
        self.found.append(FieldError(code, field, params))

    def length(self, value: str, field: str, limit: int, *, required: bool = False) -> str:
        if required and not value.strip():
            self.add(ErrorCode.NO_TEXT, field)
        elif len(value) > limit:
            self.add(ErrorCode.TOO_LONG, field, max_length=limit)
        return value

    def choice(self, value: str, field: str, choices: tuple[str, ...]) -> str:
        if value not in choices:
            self.add(ErrorCode.INVALID_OPTION, field)
        return value

    def number(self, value: str, field: str, low: int, high: int | None = None) -> int:
        try:
            number = int(value.strip() or '0')
        except ValueError:
            number = low - 1
        if number < low or (high is not None and number > high):
            limit = f'{low} - {high}' if high is not None else f'at least {low}'
            self.add(ErrorCode.BAD_NUMBER, field, range=limit)
        return number

    def raise_any(self) -> None:
        if self.found:
            raise InaError(*self.found)


def name_errors(name: str) -> list[FieldError]:
    if not NAME.match(name) or name.lower() in RESERVED:
        return [FieldError(ErrorCode.BAD_COMMUNITY_NAME, 'name')]
    return []


def text_from_form(
    form: CommunityForm, current: CommunitySettings, errors: _Errors
) -> CommunityText:
    """Validate and render everything but the name; errors are collected, not raised."""
    title = errors.length(form.title.strip(), 'title', MAX_TITLE, required=True)
    public_description = errors.length(
        form.public_description, 'public_description', MAX_PUBLIC_DESCRIPTION
    )
    sidebar = errors.length(form.description, 'description', MAX_SIDEBAR)
    submit_text = errors.length(form.submit_text, 'submit_text', MAX_SUBMIT_TEXT)
    sort = form.suggested_comment_sort or None
    if sort is not None:
        errors.choice(sort, 'suggested_comment_sort', COMMENT_SORTS)
    settings = msgspec.structs.replace(
        current,
        link_type=errors.choice(form.link_type, 'link_type', LINK_TYPES),
        submit_text=submit_text,
        submit_text_html=markdown.render(submit_text),
        submit_link_label=errors.length(form.submit_link_label, 'submit_link_label', MAX_LABEL),
        submit_text_label=errors.length(form.submit_text_label, 'submit_text_label', MAX_LABEL),
        show_media=_checked(form.show_media),
        show_media_preview=_checked(form.show_media_preview),
        allow_top=_checked(form.allow_top),
        collapse_deleted_comments=_checked(form.collapse_deleted_comments),
        exclude_banned_modqueue=_checked(form.exclude_banned_modqueue),
        public_traffic=_checked(form.public_traffic),
        spam_links=errors.choice(form.spam_links, 'spam_links', SPAM_LEVELS),
        spam_selfposts=errors.choice(form.spam_selfposts, 'spam_selfposts', SPAM_LEVELS),
        spam_comments=errors.choice(form.spam_comments, 'spam_comments', SPAM_LEVELS),
        comment_score_hide_mins=errors.number(
            form.comment_score_hide_mins, 'comment_score_hide_mins', 0, MAX_SCORE_HIDE_MINS
        ),
        suggested_comment_sort=sort,
        wikimode=errors.choice(form.wikimode, 'wikimode', WIKI_MODES),
        wiki_edit_karma=errors.number(form.wiki_edit_karma, 'wiki_edit_karma', 0),
        wiki_edit_age=errors.number(form.wiki_edit_age, 'wiki_edit_age', 0),
    )
    return CommunityText(
        title=title,
        description_md=public_description,
        description_html=markdown.render(public_description),
        sidebar_md=sidebar,
        sidebar_html=markdown.render(sidebar),
        type=errors.choice(form.type, 'type', TYPES),
        nsfw=_checked(form.over_18),
        settings=settings,
    )


def can_create(settings: Settings, account: Account, *, now: datetime | None = None) -> bool:
    age = (now or datetime.now(UTC)) - account.created
    if age < timedelta(days=settings.create_community_account_age_days):
        return False
    return (
        account.link_karma >= settings.create_community_link_karma
        or account.comment_karma >= settings.create_community_comment_karma
    )


async def create(
    communities: CommunityRepo, settings: Settings, account: Account, form: CommunityForm
) -> Community:
    """Create a community with ``account`` as its first moderator, holding every permission."""
    if not can_create(settings, account):
        raise InaError.of(ErrorCode.CANT_CREATE_COMMUNITY, '')
    errors = _Errors()
    errors.found.extend(name_errors(form.name))
    text = text_from_form(form, CommunitySettings(), errors)
    if not errors.found and await communities.by_name(form.name):
        errors.add(ErrorCode.COMMUNITY_EXISTS, 'name')
    errors.raise_any()
    try:
        community = await communities.create(name=form.name, text=text, creator_id=account.id)
    except NameTakenError:
        raise InaError.of(ErrorCode.COMMUNITY_EXISTS, 'name') from None
    await communities.add_moderator(
        community.id, account.id, permissions=ModPermission.ALL, added_by=None
    )
    return community


async def update(
    communities: CommunityRepo, community: Community, form: CommunityForm
) -> Community:
    errors = _Errors()
    text = text_from_form(form, community.settings, errors)
    errors.raise_any()
    return await communities.update(community.id, text)


async def set_subscription(
    subscriptions: SubscriptionRepo,
    account: Account,
    community: Community,
    *,
    subscribe: bool,
    standing: Standing,
    defaults: list[int],
) -> bool:
    """Subscribe or unsubscribe; return whether this made a new subscription to ``community``.

    A first subscription brings the defaults with it, as on r2, so the front page does not shrink
    to a single community.
    """
    if subscribe:
        if not can(account, Action.SUBSCRIBE, community, standing=standing):
            raise InaError.of(ErrorCode.COMMUNITY_NOTALLOWED, 'community', status_code=403)
        already = await subscriptions.subscribed_among(account.id, [community.id])
        if not await subscriptions.subscribed_ids(account.id, limit=1):
            for default_id in defaults:
                await subscriptions.subscribe(account.id, default_id)
        await subscriptions.subscribe(account.id, community.id)
        return not already
    await subscriptions.unsubscribe(account.id, community.id)
    return False
