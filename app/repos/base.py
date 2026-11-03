"""Records and one Protocol per repository. Services depend on these, never on an implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntFlag, StrEnum
from typing import Protocol
from uuid import UUID

import msgspec

from app.ids import Fullname, Kind


class Prefs(msgspec.Struct, frozen=True, kw_only=True):
    """Account preferences, stored as ``accounts.prefs``; defaults are r2's."""

    numsites: int = 25
    newwindow: bool = False
    clickgadget: bool = True
    public_votes: bool = False
    hide_from_robots: bool = False
    hide_ups: bool = False
    hide_downs: bool = False
    # None shows everything regardless of score.
    min_link_score: int | None = -4
    min_comment_score: int | None = -4
    num_comments: int = 100
    highlight_controversial: bool = False
    default_comment_sort: str = 'confidence'
    lang: str = 'en'
    content_langs: tuple[str, ...] = ('en',)
    over_18: bool = False
    compress: bool = False
    domain_details: bool = False
    no_profanity: bool = True
    label_nsfw: bool = True
    show_stylesheets: bool = True
    show_flair: bool = True
    show_link_flair: bool = True
    mark_messages_read: bool = True
    threaded_messages: bool = True
    collapse_read_messages: bool = False
    email_messages: bool = False
    private_feeds: bool = True
    highlight_new_comments: bool = True
    monitor_mentions: bool = True
    collapse_left_bar: bool = False
    ignore_suggested_sort: bool = False
    media: str = 'community'
    media_preview: str = 'community'
    threaded_modmail: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Account:
    id: int
    name: str
    password_hash: str
    email: str | None
    email_verified: bool
    created: datetime
    link_karma: int
    comment_karma: int
    prefs: Prefs
    deleted: bool
    # For good: it can't log in. r2's suspension.
    suspended: bool
    # For now: it may read, but not post, until ``timeout_expires`` (None: until lifted).
    in_timeout: bool
    inbox_count: int
    session_version: int
    # When the account last opened its modmail.
    modmsgtime: datetime | None = None
    # A site admin with admin mode on: only then do its admin powers act.
    is_admin: bool = False
    timeout_expires: datetime | None = None

    @property
    def fullname(self) -> Fullname:
        return Fullname(Kind.ACCOUNT, self.id)


class NameTakenError(Exception):
    """An account with that name (case-insensitively) already exists."""


class AccountRepo(Protocol):
    async def by_id(self, account_id: int) -> Account | None: ...

    async def recount_karma(self, account_id: int) -> None:
        """Link and comment karma from the scores of the account's links and comments."""
        ...

    async def relate(self, account_id: int, target_id: int, kind: str, *, note: str = '') -> bool:
        """Befriend (``friend``) or block (``enemy``); return False if it already was."""
        ...

    async def unrelate(self, account_id: int, target_id: int, kind: str) -> bool: ...

    async def related(self, account_id: int, kind: str) -> list[Member]:
        """The account's friends or blocks, by name."""
        ...

    async def related_among(
        self, account_id: int, kind: str, target_ids: list[int]
    ) -> set[int]: ...

    async def related_ids(self, account_id: int, kind: str) -> list[int]: ...

    async def by_name(self, name: str) -> Account | None:
        """Look up by name, ignoring case; deleted accounts are returned too."""
        ...

    async def create(self, *, name: str, password_hash: str, email: str | None) -> Account:
        """Insert an account; raise ``NameTakenError`` if the name is in use."""
        ...

    async def set_password(self, account_id: int, password_hash: str) -> int:
        """Replace the hash and bump the session version; return the new version."""
        ...

    async def rehash_password(self, account_id: int, password_hash: str) -> None:
        """Store a stronger hash of the same password; sessions stay valid."""
        ...

    async def set_email(self, account_id: int, email: str | None) -> None: ...

    async def set_prefs(self, account_id: int, prefs: Prefs) -> None: ...

    async def set_admin(self, account_id: int, *, admin: bool) -> None: ...

    async def admins(self) -> list[str]:
        """The site admins' names."""
        ...

    async def suspend(self, account_id: int, *, until: datetime | None) -> None:
        """Suspend the account until ``until``, or for good (it can't log in) if None."""
        ...

    async def unsuspend(self, account_id: int) -> None: ...

    async def newest(self, *, limit: int, before_id: int | None = None) -> list[Account]:
        """Newest first, deleted ones too."""
        ...

    async def bump_session_version(self, account_id: int) -> int: ...

    async def set_inbox_count(self, account_id: int, count: int) -> None: ...

    async def set_modmsgtime(self, account_id: int, when: datetime) -> None: ...


class ModPermission(IntFlag):
    """A moderator's permissions in one community, as r2 named them."""

    ACCESS = 1
    CONFIG = 2
    FLAIR = 4
    MAIL = 8
    POSTS = 16
    WIKI = 32
    ALL = ACCESS | CONFIG | FLAIR | MAIL | POSTS | WIKI


class CommunitySettings(msgspec.Struct, frozen=True, kw_only=True):
    """``communities.settings``; defaults are r2's."""

    link_type: str = 'any'
    submit_text: str = ''
    submit_text_html: str = ''
    submit_link_label: str = ''
    submit_text_label: str = ''
    lang: str = 'en'
    wikimode: str = 'disabled'
    wiki_edit_karma: int = 100
    wiki_edit_age: int = 0
    suggested_comment_sort: str | None = None
    comment_score_hide_mins: int = 0
    archive_age_days: int = 180
    allow_top: bool = True
    show_media: bool = False
    show_media_preview: bool = True
    public_traffic: bool = False
    exclude_banned_modqueue: bool = False
    spam_links: str = 'high'
    spam_selfposts: str = 'high'
    spam_comments: str = 'low'
    flair_enabled: bool = True
    flair_position: str = 'right'
    link_flair_position: str = ''
    flair_self_assign_enabled: bool = False
    link_flair_self_assign_enabled: bool = False
    collapse_deleted_comments: bool = False
    # At most two stickied links.
    sticky_fullnames: tuple[str, ...] = ()
    # The uploaded header image's object key, its size, and its hover text, as r2's header.
    header_img: str = ''
    header_size: tuple[int, int] | None = None
    header_title: str = ''
    icon_img: str = ''
    banner_img: str = ''
    key_color: str = ''
    quarantine: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CommunityText:
    """Everything a moderator edits on the settings page, with the markdown already rendered."""

    title: str
    description_md: str
    description_html: str
    sidebar_md: str
    sidebar_html: str
    type: str
    nsfw: bool
    settings: CommunitySettings


@dataclass(frozen=True, slots=True, kw_only=True)
class Community:
    id: int
    name: str
    title: str
    description_md: str
    description_html: str
    sidebar_md: str
    sidebar_html: str
    type: str
    nsfw: bool
    settings: CommunitySettings
    subscriber_count: int
    created: datetime
    # Bouncer's rules, as YAML.
    bouncer_rules: str = ''
    # Names the compiled stylesheet's version; empty when there is none.
    stylesheet_hash: str = ''
    # Closed by the site's admins.
    banned: bool = False

    @property
    def fullname(self) -> Fullname:
        return Fullname(Kind.COMMUNITY, self.id)

    @property
    def path(self) -> str:
        return f'/in/{self.name}/'


@dataclass(frozen=True, slots=True, kw_only=True)
class Moderator:
    account_id: int
    name: str
    permissions: ModPermission
    added: datetime


class CommunityRepo(Protocol):
    async def by_id(self, community_id: int) -> Community | None: ...

    async def by_ids(self, community_ids: list[int]) -> dict[int, Community]: ...

    async def max_id(self) -> int: ...

    async def by_name(self, name: str) -> Community | None:
        """Look up by name, ignoring case."""
        ...

    async def create(self, *, name: str, text: CommunityText, creator_id: int) -> Community:
        """Insert a community; raise ``NameTakenError`` if the name is in use."""
        ...

    async def update(self, community_id: int, text: CommunityText) -> Community: ...

    async def set_settings(self, community_id: int, settings: CommunitySettings) -> None: ...

    async def set_flags(
        self, community_id: int, *, banned: bool | None = None, nsfw: bool | None = None
    ) -> None:
        """Set what is given; None leaves it as it is."""
        ...

    async def set_bouncer_rules(self, community_id: int, rules: str) -> None: ...

    async def popular(self, *, limit: int, after: Community | None) -> list[Community]:
        """Most subscribers first; ``after`` continues from that community."""
        ...

    async def newest(
        self, *, limit: int, after: Community | None, include_banned: bool = False
    ) -> list[Community]: ...

    async def moderators(self, community_id: int) -> list[Moderator]:
        """In the order they were added."""
        ...

    async def permissions(self, community_id: int, account_id: int) -> ModPermission | None:
        """A moderator's permissions, or None if the account is not a moderator."""
        ...

    async def add_moderator(
        self,
        community_id: int,
        account_id: int,
        *,
        permissions: ModPermission,
        added_by: int | None,
    ) -> None: ...

    async def moderated_by(
        self, account_id: int, *, limit: int, after: Community | None
    ) -> list[Community]:
        """Communities the account moderates, by name."""
        ...

    async def ids_by_names(self, names: list[str]) -> list[int]:
        """Ids of the named communities that exist, in the order given."""
        ...

    async def is_muted(self, community_id: int, account_id: int) -> bool:
        """Muted from the community's modmail, and the mute has not run out."""
        ...

    async def standing(
        self, community_id: int, account_id: int
    ) -> tuple[ModPermission | None, bool, bool]:
        """The account's place in a community.

        Its mod permissions (None if not a moderator), whether it is an approved submitter, and
        whether a ban is in force.
        """
        ...

    async def moderated_ids(self, account_id: int, *, needing: ModPermission) -> list[int]:
        """Communities where the account is a moderator holding ``needing``."""
        ...

    async def defaults(self, *, limit: int) -> list[int]:
        """The front page for anyone without subscriptions: the biggest listed SFW public ones."""
        ...

    async def excluded_from_search(
        self, account_id: int | None, *, include_nsfw: bool
    ) -> list[int]:
        """Communities whose links a search leaves out for this account.

        Private ones it neither moderates nor submits to, and NSFW ones unless ``include_nsfw``.
        """
        ...


class SubscriptionRepo(Protocol):
    async def subscribe(self, account_id: int, community_id: int) -> bool:
        """Subscribe and count it; return False if already subscribed."""
        ...

    async def unsubscribe(self, account_id: int, community_id: int) -> bool:
        """Unsubscribe and count it; return False if not subscribed."""
        ...

    async def subscribed_among(self, account_id: int, community_ids: list[int]) -> set[int]: ...

    async def subscribed_ids(self, account_id: int, *, limit: int) -> list[int]: ...

    async def subscribed(
        self, account_id: int, *, limit: int, after: Community | None
    ) -> list[Community]:
        """The account's subscriptions, by name."""
        ...

    async def opted_in(self, account_id: int, community_id: int) -> bool:
        """Whether the account chose to see this quarantined community."""
        ...

    async def set_opt_in(self, account_id: int, community_id: int, *, opted_in: bool) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Link:
    id: int
    community_id: int
    community_name: str
    author_id: int
    author_name: str
    title: str
    # The submitted URL; empty for self posts, whose URL is their permalink.
    url: str
    domain: str
    is_self: bool
    selftext_md: str
    selftext_html: str
    nsfw: bool
    spoiler: bool
    flair_text: str | None
    flair_css: str | None
    score: int
    upvotes: int
    downvotes: int
    hot_score: float
    controversy_score: float
    comment_count: int
    created: datetime
    edited: datetime | None
    removed: bool
    # Who removed it: filter, mod or admin; None if it isn't removed.
    removed_by: str | None = None
    spam: bool
    deleted: bool
    locked: bool
    stickied: bool
    suggested_sort: str | None
    contest_mode: bool
    sendreplies: bool
    sticky_comment_id: int | None
    distinguished: str | None
    approved_by: int | None
    ignore_reports: bool
    reported: int
    # The thumbnail's object key, and the size to show it at; empty until the media job runs.
    thumbnail: str = ''
    thumbnail_width: int = 0
    thumbnail_height: int = 0
    # The site whose player the link opens in, and the post there; empty until the media job
    # finds one. See Embed.
    embed_provider: str = ''
    embed_id: str = ''
    embed_start: int = 0

    @property
    def fullname(self) -> Fullname:
        return Fullname(Kind.LINK, self.id)

    @property
    def embed(self) -> Embed | None:
        if not self.embed_provider:
            return None
        return Embed(self.embed_provider, self.embed_id, self.embed_start)


@dataclass(frozen=True, slots=True)
class Embed:
    """A post on another site that a link plays in that site's player (app/services/embeds.py).

    ``id`` is whatever that site's player needs, e.g. a YouTube video ID or a Bluesky author's
    DID and record key; ``start`` is where a video starts, in seconds.
    """

    provider: str
    id: str
    start: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class NewLink:
    community_id: int
    author_id: int
    title: str
    url: str
    domain: str
    is_self: bool
    selftext_md: str
    selftext_html: str
    nsfw: bool
    sendreplies: bool
    ip: str
    created: datetime
    upvotes: int
    downvotes: int
    hot_score: float
    controversy_score: float


class Sort(StrEnum):
    HOT = 'hot'
    NEW = 'new'
    RISING = 'rising'
    TOP = 'top'
    CONTROVERSIAL = 'controversial'


@dataclass(frozen=True, slots=True, kw_only=True)
class ListingQuery:
    """One page of links. ``community_ids`` None means r/all: every listed public community."""

    community_ids: tuple[int, ...] | None
    sort: Sort
    # For top and controversial: only links created since then.
    since: datetime | None = None
    # Continue past, or back before, this link in the same order.
    after: Link | None = None
    before: Link | None = None
    limit: int
    include_nsfw: bool = True
    min_score: int | None = None
    # Only links to this domain, as /domain/{domain} lists them.
    domain: str | None = None
    # Only links by these accounts, as /in/friends lists them.
    author_ids: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class ProfileAnchor:
    """Where a profile page ends: the last thing's sort value and when it was made."""

    value: float
    created: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileQuery:
    """One page of an account's links or comments, for its profile."""

    author_id: int
    sort: Sort
    since: datetime | None
    limit: int
    after: ProfileAnchor | None
    # The author's own profile shows what moderators removed; nobody's shows what was deleted.
    include_removed: bool
    # Communities closed to the viewer.
    excluded: tuple[int, ...] = ()


class LinkRepo(Protocol):
    async def create(self, link: NewLink) -> Link: ...

    async def by_id(self, link_id: int) -> Link | None: ...

    async def by_author(self, query: ProfileQuery) -> list[Link]:
        """An account's links for its profile, in the query's order."""
        ...

    async def set_thumbnail(self, link_id: int, key: str, size: tuple[int, int]) -> None: ...

    async def set_embed(self, link_id: int, embed: Embed) -> None: ...

    async def by_ids(self, link_ids: list[int]) -> dict[int, Link]: ...

    async def by_url(self, community_id: int, url: str) -> Link | None:
        """A live link to ``url`` already in the community, for ALREADY_SUB."""
        ...

    async def listing(self, query: ListingQuery) -> list[int]:
        """Link ids in listing order. Removed, spam and deleted links are never listed."""
        ...

    async def rising(self, query: ListingQuery) -> list[int]:
        """Links since ``query.since`` by score per hour of age, as r2's rising ranked them."""
        ...

    async def same_url(self, url: str, *, exclude: int, limit: int) -> list[Link]:
        """Other live links to ``url`` in any community, newest first: r2's other discussions."""
        ...

    async def recount(self, link_id: int) -> Link | None:
        """Recount votes for one link and rewrite its score, hot and controversy columns."""
        ...

    async def rebuild_scores(self, first_id: int, last_id: int) -> int:
        """Recount every link in an id range; return how many rows changed."""
        ...

    async def max_id(self) -> int: ...

    async def recount_comments(self, link_id: int) -> Link | None:
        """Rewrite ``comment_count`` from the comments that are not deleted."""
        ...

    async def edit(self, link_id: int, selftext_md: str, selftext_html: str, at: datetime) -> None:
        """Replace a self post's text, marking it edited ``at``."""
        ...

    async def delete(self, link_id: int) -> None:
        """Mark a link deleted by its author; the row stays, as on r2."""
        ...


class VoteRepo(Protocol):
    async def cast(self, account_id: int, thing: Fullname, direction: int) -> None:
        """Record a vote of -1, 0 or 1, replacing any earlier one."""
        ...

    async def voted_links(
        self, account_id: int, direction: int, *, limit: int, before: datetime | None
    ) -> list[tuple[int, datetime]]:
        """Links the account voted on this way, newest vote first, with when."""
        ...

    async def voted_at(self, account_id: int, thing: Fullname) -> datetime | None:
        """When the account last voted on the thing, if it has."""
        ...

    async def directions(self, account_id: int, kind: Kind, thing_ids: list[int]) -> dict[int, int]:
        """The account's non-zero votes on those things."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Comment:
    id: int
    link_id: int
    parent_id: int | None
    # The base36 ids from the top-level comment down to this one, dot-separated.
    path: str
    depth: int
    author_id: int
    author_name: str
    community_id: int
    community_name: str
    body_md: str
    body_html: str
    score: int
    upvotes: int
    downvotes: int
    confidence: float
    controversy_score: float
    created: datetime
    edited: datetime | None
    removed: bool
    # Who removed it: filter, mod or admin; None if it isn't removed.
    removed_by: str | None = None
    spam: bool
    deleted: bool
    distinguished: str | None
    sendreplies: bool
    approved_by: int | None
    ignore_reports: bool
    reported: int

    @property
    def fullname(self) -> Fullname:
        return Fullname(Kind.COMMENT, self.id)

    @property
    def parent_fullname(self) -> Fullname:
        if self.parent_id is None:
            return Fullname(Kind.LINK, self.link_id)
        return Fullname(Kind.COMMENT, self.parent_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class NewComment:
    link_id: int
    parent: Comment | None
    author_id: int
    body_md: str
    body_html: str
    sendreplies: bool
    ip: str
    created: datetime
    upvotes: int
    downvotes: int
    confidence: float
    controversy_score: float


@dataclass(frozen=True, slots=True)
class RemovedThing:
    """A link or comment removed by ModerationRepo.remove_all_by, and where it was."""

    thing: Fullname
    link_id: int
    community_id: int


class TreeRow(msgspec.Struct, frozen=True, array_like=True):
    """What ordering a thread needs from each comment, small enough to cache by the thousand."""

    id: int
    parent_id: int | None
    score: int
    confidence: float
    controversy_score: float
    created: float
    author_id: int
    distinguished: str | None


class CommentRepo(Protocol):
    async def create(self, comment: NewComment) -> Comment: ...

    async def by_author(self, query: ProfileQuery) -> list[Comment]:
        """An account's comments for its profile, in the query's order."""
        ...

    async def by_id(self, comment_id: int) -> Comment | None: ...

    async def by_ids(self, comment_ids: list[int]) -> dict[int, Comment]: ...

    async def for_link(self, link_id: int) -> list[Comment]:
        """Every comment in a thread, in one query; for threads small enough to sort in Python."""
        ...

    async def tree_rows(self, link_id: int) -> list[TreeRow]: ...

    async def recount(self, comment_id: int) -> Comment | None:
        """Recount votes for one comment and rewrite its score, confidence and controversy."""
        ...

    async def rebuild_scores(self, first_id: int, last_id: int) -> int: ...

    async def max_id(self) -> int: ...

    async def edit(self, comment_id: int, body_md: str, body_html: str, at: datetime) -> None: ...

    async def delete(self, comment_id: int) -> None:
        """Mark a comment deleted by its author; its replies stay under it."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Message:
    id: int
    from_id: int
    from_name: str
    to_id: int | None
    to_name: str | None
    community_id: int | None
    community_name: str | None
    parent_id: int | None
    first_message_id: int
    subject: str
    body_md: str
    body_html: str
    read: bool
    created: datetime
    from_community: bool
    del_on_recipient: bool

    @property
    def fullname(self) -> Fullname:
        return Fullname(Kind.MESSAGE, self.id)


@dataclass(frozen=True, slots=True, kw_only=True)
class NewMessage:
    from_id: int
    to_id: int | None
    community_id: int | None
    parent: Message | None
    subject: str
    body_md: str
    body_html: str
    from_community: bool
    ip: str
    created: datetime


class Box(StrEnum):
    """The tabs of /message, by their r2 path names."""

    INBOX = 'inbox'
    UNREAD = 'unread'
    MESSAGES = 'messages'
    COMMENTS = 'comments'
    SELFREPLY = 'selfreply'
    MENTIONS = 'mentions'
    SENT = 'sent'
    MODERATOR = 'moderator'


class EntryKind(StrEnum):
    MESSAGE = 'message'
    COMMENT_REPLY = 'comment_reply'
    POST_REPLY = 'post_reply'
    MENTION = 'mention'


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxEntry:
    """One line of an inbox: a message, or a comment that is a reply or a mention."""

    kind: EntryKind
    thing_id: int
    created: datetime
    unread: bool

    @property
    def fullname(self) -> Fullname:
        kind = Kind.MESSAGE if self.kind is EntryKind.MESSAGE else Kind.COMMENT
        return Fullname(kind, self.thing_id)


class MessageRepo(Protocol):
    async def create(self, message: NewMessage) -> Message: ...

    async def by_id(self, message_id: int) -> Message | None: ...

    async def by_ids(self, message_ids: list[int]) -> dict[int, Message]: ...

    async def conversation(self, first_message_id: int) -> list[Message]:
        """Every message in a conversation, oldest first."""
        ...

    async def inbox(
        self,
        account_id: int,
        box: Box,
        *,
        limit: int,
        after: InboxEntry | None,
        moderated: list[int],
    ) -> list[InboxEntry]:
        """Newest first. ``moderated`` are the communities whose modmail the account reads."""
        ...

    async def entry(self, account_id: int, thing: Fullname) -> InboxEntry | None:
        """The account's own inbox entry for a thing, to continue a page after it."""
        ...

    async def mark(self, account_id: int, things: list[Fullname], *, read: bool) -> None:
        """Mark things in the account's own inbox; things in anyone else's are left alone."""
        ...

    async def mark_all_read(self, account_id: int) -> None: ...

    async def unread_count(self, account_id: int) -> int: ...

    async def add_mention(self, account_id: int, comment_id: int) -> bool:
        """Put a mention in the inbox; False if it was there already."""
        ...

    async def modmail_since(self, community_ids: list[int], since: datetime | None) -> bool:
        """Whether those communities got modmail after ``since`` (any at all, if None)."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Member:
    """Someone on one of a community's lists: approved submitters, invited moderators, bans."""

    account_id: int
    name: str
    created: datetime
    # Bans only: the moderators' note, the message sent, and when it ends (None: never).
    note: str = ''
    expires: datetime | None = None
    # Invites only.
    permissions: ModPermission | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ModAction:
    id: UUID
    community_id: int
    community_name: str
    mod_name: str
    action: str
    target: Fullname | None
    details: str
    created: datetime


class Queue(StrEnum):
    """The moderation queues, by their r2 path names."""

    MODQUEUE = 'modqueue'
    REPORTS = 'reports'
    SPAM = 'spam'
    UNMODERATED = 'unmoderated'


@dataclass(frozen=True, slots=True, kw_only=True)
class QueueEntry:
    thing: Fullname
    created: datetime


class ModerationRepo(Protocol):
    async def mute(self, community_id: int, account_id: int, *, expires: datetime, by: int) -> None:
        """Mute from the community's modmail until ``expires``, as r2's muted relation."""
        ...

    async def unmute(self, community_id: int, account_id: int) -> bool: ...

    async def mutes(self, community_id: int) -> list[Member]:
        """Mutes in force, newest first, with when each ends."""
        ...

    async def ban(
        self,
        community_id: int,
        account_id: int,
        *,
        note: str,
        message: str,
        expires: datetime | None,
        by: int,
    ) -> None:
        """Ban, or change an existing ban's note and length."""
        ...

    async def unban(self, community_id: int, account_id: int) -> bool: ...

    async def bans(self, community_id: int) -> list[Member]:
        """Bans in force, newest first."""
        ...

    async def add_contributor(self, community_id: int, account_id: int, *, by: int) -> bool: ...

    async def remove_contributor(self, community_id: int, account_id: int) -> bool: ...

    async def contributors(self, community_id: int) -> list[Member]: ...

    async def wiki_standing(self, community_id: int, account_id: int) -> tuple[bool, bool]:
        """Whether the account is a wiki contributor, and whether a wiki ban is in force."""
        ...

    async def add_wiki_contributor(
        self, community_id: int, account_id: int, *, by: int
    ) -> bool: ...

    async def remove_wiki_contributor(self, community_id: int, account_id: int) -> bool: ...

    async def wiki_contributors(self, community_id: int) -> list[Member]: ...

    async def wiki_ban(
        self, community_id: int, account_id: int, *, note: str, expires: datetime | None, by: int
    ) -> None:
        """Ban from editing the wiki, or change an existing wiki ban's note and length."""
        ...

    async def wiki_unban(self, community_id: int, account_id: int) -> bool: ...

    async def wiki_bans(self, community_id: int) -> list[Member]:
        """Wiki bans in force, newest first."""
        ...

    async def invite(
        self, community_id: int, account_id: int, *, permissions: ModPermission, by: int
    ) -> None: ...

    async def take_invite(self, community_id: int, account_id: int) -> ModPermission | None:
        """Remove a pending invite, returning its permissions, or None if there was none."""
        ...

    async def invites(self, community_id: int) -> list[Member]: ...

    async def remove_moderator(self, community_id: int, account_id: int) -> bool: ...

    async def set_permissions(
        self, community_id: int, account_id: int, permissions: ModPermission
    ) -> bool: ...

    async def log(
        self,
        community_id: int,
        mod_id: int,
        action: str,
        target: Fullname | None = None,
        details: str = '',
    ) -> None: ...

    async def actions(
        self,
        community_ids: list[int],
        *,
        limit: int,
        before_id: UUID | None = None,
        mod_name: str | None = None,
        action: str | None = None,
    ) -> list[ModAction]:
        """The modlog, newest first; ``before_id`` continues a page."""
        ...

    async def report(self, thing: Fullname, reporter_id: int, reason: str) -> bool:
        """Report a thing once per person; also rewrites its report count."""
        ...

    async def report_reasons(self, things: list[Fullname]) -> dict[Fullname, list[str]]: ...

    async def clear_reports(self, thing: Fullname) -> None: ...

    async def set_removed(
        self, thing: Fullname, *, removed: bool, spam: bool, removed_by: str | None
    ) -> None:
        """Remove (or put back) a link or comment. Removing clears any approval."""
        ...

    async def remove_all_by(self, author_id: int) -> list[RemovedThing]:
        """Remove, as an admin, every link and comment the account posted."""
        ...

    async def set_approved(self, thing: Fullname, *, by: int, at: datetime) -> None:
        """Approve a link or comment: no longer removed or spam, and its reports cleared."""
        ...

    async def set_ignore_reports(self, thing: Fullname, *, ignore: bool) -> None: ...

    async def set_distinguished(self, thing: Fullname, how: str | None) -> None: ...

    async def set_link_flags(
        self,
        link_id: int,
        *,
        locked: bool | None = None,
        stickied: bool | None = None,
        nsfw: bool | None = None,
    ) -> None: ...

    async def set_sticky_comment(self, link_id: int, comment_id: int | None) -> None: ...

    async def queue(
        self, community_ids: list[int] | None, queue: Queue, *, limit: int, after: QueueEntry | None
    ) -> list[QueueEntry]:
        """Links and comments in a moderation queue, newest first; None is every community's."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class WikiPage:
    id: int
    community_id: int
    name: str
    # 0: the community's wiki permissions; 1: only this page's editors; 2: only moderators.
    permlevel: int
    listed: bool
    content_md: str
    content_html: str
    revision_id: UUID | None
    revised_by: str | None
    revised_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WikiRevision:
    id: UUID
    page_id: int
    page_name: str
    author_id: int | None
    author_name: str | None
    content_md: str
    reason: str
    hidden: bool
    created: datetime


class WikiConflictError(Exception):
    """The page has a newer revision than the one the edit started from."""

    def __init__(self, current: WikiPage) -> None:
        super().__init__(current.name)
        self.current = current


class WikiRepo(Protocol):
    async def page(self, community_id: int, name: str) -> WikiPage | None: ...

    async def pages(self, community_id: int) -> list[WikiPage]:
        """Every page, by name."""
        ...

    async def create_page(self, community_id: int, name: str) -> WikiPage:
        """An empty page, or the page if it already exists."""
        ...

    async def revise(
        self,
        page_id: int,
        *,
        content_md: str,
        content_html: str,
        author_id: int,
        reason: str,
        previous: UUID | None,
    ) -> WikiRevision:
        """Add a revision and make it current.

        With ``previous`` given, raise ``WikiConflictError`` unless it is still the current one.
        """
        ...

    async def revision(self, page_id: int, revision_id: UUID) -> WikiRevision | None: ...

    async def revisions(
        self,
        community_id: int,
        *,
        page_id: int | None,
        limit: int,
        before: UUID | None,
        include_hidden: bool,
    ) -> list[WikiRevision]:
        """Newest first, of one page or the whole wiki; ``before`` continues a page of them."""
        ...

    async def set_hidden(self, revision_id: UUID, *, hidden: bool) -> None: ...

    async def set_page_settings(self, page_id: int, *, permlevel: int, listed: bool) -> None: ...

    async def editors(self, page_id: int) -> list[Member]: ...

    async def is_editor(self, page_id: int, account_id: int) -> bool: ...

    async def add_editor(self, page_id: int, account_id: int) -> None: ...

    async def remove_editor(self, page_id: int, account_id: int) -> None: ...


class FlairKind(StrEnum):
    """r2's flair types."""

    USER = 'USER_FLAIR'
    LINK = 'LINK_FLAIR'


@dataclass(frozen=True, slots=True, kw_only=True)
class UserFlair:
    account_id: int
    name: str
    text: str | None
    css_class: str | None
    # The user shows their flair here; r2's flair_<name>_enabled.
    enabled: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class FlairTemplate:
    id: UUID
    kind: FlairKind
    text: str
    css_class: str
    text_editable: bool
    position: int


class TooManyTemplatesError(Exception):
    """r2 allowed 350 flair templates of each kind per community."""


class Mark(StrEnum):
    SAVED = 'saved'
    HIDDEN = 'hidden'


class MarkRepo(Protocol):
    """Things an account saved or hid, as r2's /api/save and /api/hide."""

    async def add(self, account_id: int, mark: Mark, thing: Fullname) -> None: ...

    async def remove(self, account_id: int, mark: Mark, thing: Fullname) -> None: ...

    async def among(self, account_id: int, mark: Mark, things: list[Fullname]) -> set[Fullname]:
        """Which of ``things`` the account marked."""
        ...

    async def page(
        self, account_id: int, mark: Mark, *, limit: int, before: datetime | None
    ) -> list[Fullname]:
        """The account's marked things, newest mark first, marked before ``before``."""
        ...

    async def marked_at(self, account_id: int, mark: Mark, thing: Fullname) -> datetime | None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class AdminAction:
    id: UUID
    admin_name: str
    action: str
    target: Fullname | None
    # The account's or community's name, when the target is one.
    target_name: str | None
    details: str
    created: datetime


class AdminRepo(Protocol):
    """What site admins did, newest first."""

    async def log(
        self, admin_id: int, action: str, target: Fullname | None = None, details: str = ''
    ) -> None: ...

    async def actions(self, *, limit: int, before_id: UUID | None = None) -> list[AdminAction]: ...


class AppKind(StrEnum):
    WEB = 'web'
    INSTALLED = 'installed'
    SCRIPT = 'script'


@dataclass(frozen=True, slots=True, kw_only=True)
class OAuthClient:
    id: str
    owner_id: int
    name: str
    description: str
    about_url: str
    redirect_uri: str
    kind: AppKind
    secret_hash: str | None
    created: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class AppFields:
    name: str
    description: str
    about_url: str
    redirect_uri: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OAuthToken:
    id: int
    client_id: str
    account_id: int | None
    scopes: frozenset[str]
    access_expires: datetime
    # Whether the grant can be refreshed: r2's duration=permanent.
    refreshable: bool
    created: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Authorization:
    """An app an account let in, with every scope it holds across its grants."""

    client: OAuthClient
    scopes: frozenset[str]
    since: datetime


class OAuthRepo(Protocol):
    """Registered apps and the tokens granted to them. Secrets and tokens are kept as hashes."""

    async def add_client(
        self,
        client_id: str,
        owner_id: int,
        kind: AppKind,
        fields: AppFields,
        secret_hash: str | None,
    ) -> OAuthClient: ...

    async def client(self, client_id: str) -> OAuthClient | None: ...

    async def owned(self, owner_id: int) -> list[OAuthClient]: ...

    async def update_client(self, client_id: str, fields: AppFields) -> None: ...

    async def set_secret(self, client_id: str, secret_hash: str) -> None: ...

    async def delete_client(self, client_id: str) -> None:
        """Delete the app and every token granted to it."""
        ...

    async def add_token(
        self,
        client_id: str,
        account_id: int | None,
        scopes: frozenset[str],
        *,
        access_hash: str,
        access_expires: datetime,
        refresh_hash: str | None,
    ) -> OAuthToken: ...

    async def by_access(self, access_hash: str) -> OAuthToken | None: ...

    async def by_refresh(self, refresh_hash: str) -> OAuthToken | None: ...

    async def rotate(
        self, token_id: int, *, access_hash: str, access_expires: datetime, refresh_hash: str
    ) -> None: ...

    async def delete_token(self, token_id: int) -> None: ...

    async def authorizations(self, account_id: int) -> list[Authorization]: ...

    async def revoke_client(self, account_id: int, client_id: str) -> None:
        """Delete every token the account granted the app."""
        ...


class FlairRepo(Protocol):
    async def user_flair(self, community_id: int, account_id: int) -> UserFlair | None: ...

    async def user_flairs(self, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], UserFlair]:
        """Flair for many (community id, account id) pairs at once."""
        ...

    async def flair_list(
        self, community_id: int, *, limit: int, after: str | None, name: str | None
    ) -> list[UserFlair]:
        """Users with flair, by name; ``after`` continues after that name."""
        ...

    async def set_user_flair(
        self, community_id: int, account_id: int, *, text: str | None, css_class: str | None
    ) -> None: ...

    async def set_flair_enabled(
        self, community_id: int, account_id: int, *, enabled: bool
    ) -> None: ...

    async def templates(self, community_id: int, kind: FlairKind) -> list[FlairTemplate]: ...

    async def template(self, community_id: int, template_id: UUID) -> FlairTemplate | None: ...

    async def save_template(
        self,
        community_id: int,
        kind: FlairKind,
        *,
        template_id: UUID | None,
        text: str,
        css_class: str,
        text_editable: bool,
    ) -> FlairTemplate:
        """Change a template, or add one at the end; ``TooManyTemplatesError`` past the limit."""
        ...

    async def delete_template(self, community_id: int, template_id: UUID) -> bool: ...

    async def clear_templates(self, community_id: int, kind: FlairKind) -> None: ...

    async def set_link_flair(
        self, link_id: int, *, text: str | None, css_class: str | None
    ) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class CommunityImage:
    """An image uploaded for the stylesheet, which uses it as ``url(%%name%%)``."""

    name: str
    key: str
    width: int
    height: int
    bytes: int


class TooManyImagesError(Exception):
    """A community may keep 50 images."""


class StylesheetRepo(Protocol):
    async def images(self, community_id: int) -> list[CommunityImage]:
        """By name."""
        ...

    async def add_image(self, community_id: int, image: CommunityImage, *, limit: int) -> None:
        """Add an image, or replace the one with its name; ``TooManyImagesError`` past ``limit``."""
        ...

    async def delete_image(self, community_id: int, name: str) -> bool: ...

    async def stylesheet(self, community_id: int) -> str | None:
        """The compiled stylesheet, as served."""
        ...

    async def set_stylesheet(self, community_id: int, css: str, version: str) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Multi:
    """r2's LabeledMulti."""

    id: int
    owner_id: int
    owner_name: str
    name: str
    display_name: str
    description_md: str
    description_html: str
    visibility: str
    key_color: str
    weighting_scheme: str
    copied_from: str | None
    created: datetime
    # (id, name) of each community in it, by name.
    communities: tuple[tuple[int, str], ...] = ()

    @property
    def path(self) -> str:
        return f'/user/{self.owner_name}/m/{self.name}'


@dataclass(frozen=True, slots=True, kw_only=True)
class MultiFields:
    """What an owner sets on a multi."""

    display_name: str
    description_md: str
    description_html: str
    visibility: str
    key_color: str
    weighting_scheme: str


class MultiExistsError(Exception):
    """The owner already has a multi of that name."""


class MultiRepo(Protocol):
    async def by_path(self, owner: str, name: str) -> Multi | None:
        """By owner name and multi name, ignoring case."""
        ...

    async def by_owner(self, owner_id: int) -> list[Multi]:
        """By name."""
        ...

    async def create(
        self, owner_id: int, name: str, fields: MultiFields, *, copied_from: str | None = None
    ) -> int:
        """Return the new multi's id; ``MultiExistsError`` if the name is taken."""
        ...

    async def update(self, multi_id: int, fields: MultiFields) -> None: ...

    async def set_communities(self, multi_id: int, community_ids: list[int]) -> None: ...

    async def add_community(self, multi_id: int, community_id: int) -> None: ...

    async def remove_community(self, multi_id: int, community_id: int) -> None: ...

    async def delete(self, multi_id: int) -> None: ...
