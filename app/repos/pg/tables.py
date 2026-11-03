"""SQLAlchemy table definitions; Alembic autogenerates migrations from this metadata."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.sql import expression, func, text
from sqlalchemy.types import UserDefinedType


class Ltree(UserDefinedType[str]):
    """Postgres ``ltree``: a dotted path of labels, here the base36 ids from the root comment."""

    cache_ok = True

    def get_col_spec(self, **_: object) -> str:
        return 'LTREE'


metadata = MetaData(
    naming_convention={
        'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
        'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
        'ck': 'ck_%(table_name)s_%(constraint_name)s',
        'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
        'pk': 'pk_%(table_name)s',
    }
)

accounts = Table(
    'accounts',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('name', CITEXT, nullable=False, unique=True),
    Column('password_hash', Text, nullable=False),
    Column('email', CITEXT),
    Column('email_verified', Boolean, nullable=False, server_default=expression.false()),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('link_karma', BigInteger, nullable=False, server_default='0'),
    Column('comment_karma', BigInteger, nullable=False, server_default='0'),
    Column('prefs', JSONB, nullable=False, server_default='{}'),
    Column('deleted', Boolean, nullable=False, server_default=expression.false()),
    Column('suspended', Boolean, nullable=False, server_default=expression.false()),
    # Suspended for a while (r2's timeout): it may read, but not post. None means until lifted.
    Column('in_timeout', Boolean, nullable=False, server_default=expression.false()),
    Column('timeout_expires', DateTime(timezone=True)),
    # A site admin, as r2's g.admins; granted only by scripts/admins.py, never by the site.
    Column('is_admin', Boolean, nullable=False, server_default=expression.false()),
    Column('spammer', Boolean, nullable=False, server_default=expression.false()),
    Column('inbox_count', Integer, nullable=False, server_default='0'),
    Column('modmsgtime', DateTime(timezone=True)),
    Column('otp_secret', Text),
    # Bumped to sign out every session at once (password change, logout everywhere).
    Column('session_version', Integer, nullable=False, server_default='0'),
)

communities = Table(
    'communities',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('name', CITEXT, nullable=False, unique=True),
    Column('title', Text, nullable=False),
    # The short public description, shown in listings of communities.
    Column('description_md', Text, nullable=False, server_default=''),
    Column('description_html', Text, nullable=False, server_default=''),
    Column('sidebar_md', Text, nullable=False, server_default=''),
    Column('sidebar_html', Text, nullable=False, server_default=''),
    Column('type', Text, nullable=False, server_default='public'),
    Column('nsfw', Boolean, nullable=False, server_default=expression.false()),
    # Closed by the site's admins: gone from listings and search, its pages say so.
    Column('banned', Boolean, nullable=False, server_default=expression.false()),
    Column('stylesheet_css', Text, nullable=False, server_default=''),
    # Bouncer's rules, as YAML.
    Column('bouncer_rules', Text, nullable=False, server_default=''),
    Column('stylesheet_hash', Text, nullable=False, server_default=''),
    Column('settings', JSONB, nullable=False, server_default='{}'),
    Column('subscriber_count', BigInteger, nullable=False, server_default='0'),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('creator_id', BigInteger, ForeignKey('accounts.id')),
    CheckConstraint("type in ('public', 'restricted', 'private')", name='type'),
    Index(None, 'subscriber_count', 'id'),
)

moderators = Table(
    'moderators',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    # A bitmask of app.repos.base.ModPermission.
    Column('permissions', Integer, nullable=False),
    Column('added_by', BigInteger, ForeignKey('accounts.id')),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, 'account_id'),
)

subscriptions = Table(
    'subscriptions',
    metadata,
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, 'community_id'),
)

# Accounts an account befriended or blocked: r2's friend and enemy relations.
relations = Table(
    'relations',
    metadata,
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('target_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    # friend, or enemy for a block.
    Column('type', Text, primary_key=True),
    Column('note', Text, nullable=False, server_default=''),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("type in ('friend', 'enemy')", name='type'),
)

# Quarantined communities an account chose to see, as r2's QuarantinedSubredditOptInsByAccount.
quarantine_optins = Table(
    'quarantine_optins',
    metadata,
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

links = Table(
    'links',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('community_id', BigInteger, ForeignKey('communities.id'), nullable=False),
    Column('author_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    Column('title', Text, nullable=False),
    # The submitted URL for link posts; the permalink for self posts, as on r2.
    Column('url', Text, nullable=False),
    Column('domain', Text, nullable=False),
    Column('is_self', Boolean, nullable=False),
    Column('selftext_md', Text, nullable=False, server_default=''),
    Column('selftext_html', Text, nullable=False, server_default=''),
    Column('nsfw', Boolean, nullable=False, server_default=expression.false()),
    Column('spoiler', Boolean, nullable=False, server_default=expression.false()),
    Column('flair_text', Text),
    Column('flair_css', Text),
    Column('score', BigInteger, nullable=False, server_default='0'),
    Column('upvotes', BigInteger, nullable=False, server_default='0'),
    Column('downvotes', BigInteger, nullable=False, server_default='0'),
    Column('hot_score', Double, nullable=False, server_default='0'),
    Column('controversy_score', Double, nullable=False, server_default='0'),
    Column('comment_count', BigInteger, nullable=False, server_default='0'),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('edited', DateTime(timezone=True)),
    Column('removed', Boolean, nullable=False, server_default=expression.false()),
    # Who removed it: filter, mod or admin (r2's banned_before_moderator, generalized).
    Column('removed_by', Text),
    Column('spam', Boolean, nullable=False, server_default=expression.false()),
    Column('deleted', Boolean, nullable=False, server_default=expression.false()),
    Column('locked', Boolean, nullable=False, server_default=expression.false()),
    Column('stickied', Boolean, nullable=False, server_default=expression.false()),
    Column('suggested_sort', Text),
    Column('contest_mode', Boolean, nullable=False, server_default=expression.false()),
    Column('sendreplies', Boolean, nullable=False, server_default=expression.true()),
    Column('ignore_reports', Boolean, nullable=False, server_default=expression.false()),
    Column('disable_comments', Boolean, nullable=False, server_default=expression.false()),
    # Embed, preview and thumbnail, filled in by the media job.
    Column('media', JSONB),
    Column('ip', Text),
    Column('reported', Integer, nullable=False, server_default='0'),
    # A moderator's comment pinned to the top of the thread (no FK: comments references links).
    Column('sticky_comment_id', BigInteger),
    # moderator or admin, when the author posted in that role.
    Column('distinguished', Text),
    Column('approved_by', BigInteger, ForeignKey('accounts.id')),
    Column('approved_at', DateTime(timezone=True)),
    Index('ix_links_community_hot', 'community_id', text('hot_score DESC'), text('id DESC')),
    Index('ix_links_community_new', 'community_id', text('id DESC')),
    Index('ix_links_community_created_score', 'community_id', 'created', text('score DESC')),
    Index('ix_links_hot', text('hot_score DESC'), text('id DESC')),
    Index('ix_links_created', 'created'),
    Index('ix_links_community_url', 'community_id', 'url'),
    Index('ix_links_author', 'author_id', text('id DESC')),
    Index('ix_links_domain', 'domain', text('id DESC')),
    # Other discussions of a URL; a hash index, since URLs can outgrow a btree entry.
    Index('ix_links_url', 'url', postgresql_using='hash'),
)

votes = Table(
    'votes',
    metadata,
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    # The fullname prefix of the thing voted on: t1 or t3.
    Column('thing_type', Text, primary_key=True),
    Column('thing_id', BigInteger, primary_key=True),
    Column('direction', SmallInteger, nullable=False),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint('direction in (-1, 0, 1)', name='direction'),
    CheckConstraint("thing_type in ('t1', 't3')", name='thing_type'),
    Index(None, 'thing_type', 'thing_id', 'direction'),
    # A profile's upvoted and downvoted tabs: one account's votes, newest first.
    Index('ix_votes_account_created', 'account_id', 'thing_type', text('created DESC')),
)

# What an account saved (links and comments) and hid (links), each newest first on its profile.
marks = Table(
    'marks',
    metadata,
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    # saved or hidden.
    Column('mark', Text, primary_key=True),
    Column('thing_type', Text, primary_key=True),
    Column('thing_id', BigInteger, primary_key=True),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("mark in ('saved', 'hidden')", name='mark'),
    CheckConstraint("thing_type in ('t1', 't3')", name='thing_type'),
    Index('ix_marks_account_created', 'account_id', 'mark', text('created DESC')),
)

# Apps registered to use the API; r2's OAuth2Client.
oauth_clients = Table(
    'oauth_clients',
    metadata,
    # The public client_id.
    Column('id', Text, primary_key=True),
    Column('owner_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    Column('name', Text, nullable=False),
    Column('description', Text, nullable=False, server_default=''),
    Column('about_url', Text, nullable=False, server_default=''),
    Column('redirect_uri', Text, nullable=False),
    # web and script apps keep a secret; installed apps can't, and prove themselves with PKCE.
    Column('kind', Text, nullable=False),
    # sha256 of the secret, which is shown once; None for installed apps.
    Column('secret_hash', Text),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("kind in ('web', 'installed', 'script')", name='kind'),
    Index('ix_oauth_clients_owner', 'owner_id'),
)

# A grant to an app: its access token and, for a permanent grant, its refresh token.
oauth_tokens = Table(
    'oauth_tokens',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('client_id', Text, ForeignKey('oauth_clients.id', ondelete='CASCADE'), nullable=False),
    # None when the app acts for itself, with no account.
    Column('account_id', BigInteger, ForeignKey('accounts.id')),
    # Space-separated, as OAuth writes them.
    Column('scopes', Text, nullable=False),
    # sha256 of each token; the tokens themselves are never stored.
    Column('access_hash', Text, nullable=False, unique=True),
    Column('access_expires', DateTime(timezone=True), nullable=False),
    Column('refresh_hash', Text, unique=True),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index('ix_oauth_tokens_account_client', 'account_id', 'client_id'),
)

comments = Table(
    'comments',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('link_id', BigInteger, ForeignKey('links.id'), nullable=False),
    Column('parent_id', BigInteger, ForeignKey('comments.id')),
    # The base36 ids from the top-level comment down to this one: WHERE path <@ root is a subtree.
    Column('path', Ltree, nullable=False),
    Column('depth', Integer, nullable=False),
    Column('author_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    Column('body_md', Text, nullable=False),
    Column('body_html', Text, nullable=False),
    Column('score', BigInteger, nullable=False, server_default='0'),
    Column('upvotes', BigInteger, nullable=False, server_default='0'),
    Column('downvotes', BigInteger, nullable=False, server_default='0'),
    Column('confidence', Double, nullable=False, server_default='0'),
    Column('controversy_score', Double, nullable=False, server_default='0'),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('edited', DateTime(timezone=True)),
    Column('removed', Boolean, nullable=False, server_default=expression.false()),
    Column('removed_by', Text),
    Column('spam', Boolean, nullable=False, server_default=expression.false()),
    Column('deleted', Boolean, nullable=False, server_default=expression.false()),
    # moderator or admin, when the author spoke in that role.
    Column('distinguished', Text),
    Column('approved_by', BigInteger, ForeignKey('accounts.id')),
    Column('approved_at', DateTime(timezone=True)),
    Column('sendreplies', Boolean, nullable=False, server_default=expression.true()),
    Column('ignore_reports', Boolean, nullable=False, server_default=expression.false()),
    Column('reported', Integer, nullable=False, server_default='0'),
    # Unread in the parent author's inbox.
    Column('new', Boolean, nullable=False, server_default=expression.true()),
    Column('ip', Text),
    Index('ix_comments_link', 'link_id'),
    Index('ix_comments_parent', 'parent_id'),
    Index('ix_comments_path', 'path', postgresql_using='gist'),
    Index('ix_comments_author', 'author_id', text('id DESC')),
)

messages = Table(
    'messages',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('from_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    # None for mail to a community's moderators.
    Column('to_id', BigInteger, ForeignKey('accounts.id')),
    # Set for modmail, in either direction.
    Column('community_id', BigInteger, ForeignKey('communities.id')),
    Column('parent_id', BigInteger, ForeignKey('messages.id')),
    # The message that started the conversation; itself for the first one.
    Column('first_message_id', BigInteger, ForeignKey('messages.id')),
    Column('subject', Text, nullable=False),
    Column('body_md', Text, nullable=False),
    Column('body_html', Text, nullable=False),
    Column('read', Boolean, nullable=False, server_default=expression.false()),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    # A moderator writing as the community rather than as themselves.
    Column('from_community', Boolean, nullable=False, server_default=expression.false()),
    # Names shown instead of the real ones, for admin and system messages.
    Column('display_author', Text),
    Column('display_to', Text),
    Column('to_collapse', Boolean),
    Column('author_collapse', Boolean),
    Column('del_on_recipient', Boolean, nullable=False, server_default=expression.false()),
    Column('ip', Text),
    Index('ix_messages_to', 'to_id', text('id DESC')),
    Index('ix_messages_from', 'from_id', text('id DESC')),
    Index('ix_messages_community', 'community_id', text('id DESC')),
    Index('ix_messages_first', 'first_message_id'),
)

# A username mention that reached someone's inbox. Replies are not stored: they are queries on
# comments, read and unread through comments.new.
mentions = Table(
    'mentions',
    metadata,
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('comment_id', BigInteger, ForeignKey('comments.id'), primary_key=True),
    Column('read', Boolean, nullable=False, server_default=expression.false()),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index('ix_mentions_account', 'account_id', text('created DESC')),
)

# Modmail mutes: the account cannot write to the community's moderators until it expires.
mutes = Table(
    'mutes',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('expires', DateTime(timezone=True), nullable=False),
    Column('created_by', BigInteger, ForeignKey('accounts.id')),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

bans = Table(
    'bans',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    # Shown to the moderators; r2 folded ban_reason into the note.
    Column('note', Text, nullable=False, server_default=''),
    # Sent to the banned user.
    Column('message', Text, nullable=False, server_default=''),
    # None for a permanent ban.
    Column('expires', DateTime(timezone=True)),
    Column('created_by', BigInteger, ForeignKey('accounts.id')),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Approved submitters: who may post in a restricted community, and read a private one.
contributors = Table(
    'contributors',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('added_by', BigInteger, ForeignKey('accounts.id')),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

moderator_invites = Table(
    'moderator_invites',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('permissions', Integer, nullable=False),
    Column('invited_by', BigInteger, ForeignKey('accounts.id')),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

reports = Table(
    'reports',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('thing_type', Text, nullable=False),
    Column('thing_id', BigInteger, nullable=False),
    Column('reporter_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    Column('reason', Text, nullable=False, server_default=''),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("thing_type in ('t1', 't3')", name='thing_type'),
    # One report per person per thing, as on r2.
    UniqueConstraint('thing_type', 'thing_id', 'reporter_id'),
)

# Append-only. Version 7 UUIDs sort by time, so pages continue from an id, as r2's
# time-based ModAction ids did.
modlog = Table(
    'modlog',
    metadata,
    Column('id', UUID(as_uuid=True), primary_key=True, server_default=text('uuidv7()')),
    Column('community_id', BigInteger, ForeignKey('communities.id'), nullable=False),
    Column('mod_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    Column('action', Text, nullable=False),
    # The fullname prefix of the target: t1, t2 or t3.
    Column('target_type', Text),
    Column('target_id', BigInteger),
    Column('details', Text, nullable=False, server_default=''),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index('ix_modlog_community', 'community_id', text('id DESC')),
)

# What site admins did, for the admin page; append-only, with ids as modlog's.
admin_log = Table(
    'admin_log',
    metadata,
    Column('id', UUID(as_uuid=True), primary_key=True, server_default=text('uuidv7()')),
    Column('admin_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    Column('action', Text, nullable=False),
    # The fullname prefix of the target: t1, t2, t3 or t5.
    Column('target_type', Text),
    Column('target_id', BigInteger),
    Column('details', Text, nullable=False, server_default=''),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# The wiki. A page's content is its current revision's, copied here so a view is one row.
wiki_pages = Table(
    'wiki_pages',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('community_id', BigInteger, ForeignKey('communities.id'), nullable=False),
    # Lower case, as r2 normalized page names.
    Column('name', Text, nullable=False),
    # 0: the community's wiki permissions; 1: only this page's editors; 2: only moderators.
    Column('permlevel', SmallInteger, nullable=False, server_default='0'),
    Column('listed', Boolean, nullable=False, server_default=expression.true()),
    Column('content_md', Text, nullable=False, server_default=''),
    Column('content_html', Text, nullable=False, server_default=''),
    Column('revision_id', UUID(as_uuid=True)),
    Column('revised_by', BigInteger, ForeignKey('accounts.id')),
    Column('revised_at', DateTime(timezone=True)),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint('permlevel between 0 and 2', name='permlevel'),
    UniqueConstraint('community_id', 'name'),
)

# Append-only. Version 7 UUIDs sort by time, like r2's time-based revision ids.
wiki_revisions = Table(
    'wiki_revisions',
    metadata,
    Column('id', UUID(as_uuid=True), primary_key=True, server_default=text('uuidv7()')),
    Column('page_id', BigInteger, ForeignKey('wiki_pages.id'), nullable=False),
    Column('author_id', BigInteger, ForeignKey('accounts.id')),
    Column('content_md', Text, nullable=False),
    Column('reason', Text, nullable=False, server_default=''),
    # Hidden by a moderator from everyone else's history.
    Column('hidden', Boolean, nullable=False, server_default=expression.false()),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index('ix_wiki_revisions_page', 'page_id', text('id DESC')),
)

# Who may edit a page whatever its permission level, as r2's page editors.
wiki_editors = Table(
    'wiki_editors',
    metadata,
    Column('page_id', BigInteger, ForeignKey('wiki_pages.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# r2's wikicontributor relation: may edit when the wiki is open to moderators only.
wiki_contributors = Table(
    'wiki_contributors',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('added_by', BigInteger, ForeignKey('accounts.id')),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# r2's wikibanned relation: may not edit the community's wiki at all.
wiki_bans = Table(
    'wiki_bans',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('note', Text, nullable=False, server_default=''),
    # None for a permanent ban.
    Column('expires', DateTime(timezone=True)),
    Column('created_by', BigInteger, ForeignKey('accounts.id')),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# A user's flair in one community, and whether they show it there (r2's flair_<name>_enabled).
user_flair = Table(
    'user_flair',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('account_id', BigInteger, ForeignKey('accounts.id'), primary_key=True),
    Column('text', Text),
    Column('css_class', Text),
    Column('enabled', Boolean, nullable=False, server_default=expression.true()),
    Column('updated', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# The choices offered when assigning flair, in the order moderators made them.
flair_templates = Table(
    'flair_templates',
    metadata,
    Column('id', UUID(as_uuid=True), primary_key=True, server_default=text('uuidv7()')),
    Column('community_id', BigInteger, ForeignKey('communities.id'), nullable=False),
    # r2's flair types: USER_FLAIR or LINK_FLAIR.
    Column('kind', Text, nullable=False),
    Column('text', Text, nullable=False, server_default=''),
    Column('css_class', Text, nullable=False, server_default=''),
    Column('text_editable', Boolean, nullable=False, server_default=expression.false()),
    Column('position', Integer, nullable=False),
    CheckConstraint("kind in ('USER_FLAIR', 'LINK_FLAIR')", name='kind'),
    Index('ix_flair_templates_community', 'community_id', 'kind', 'position'),
)


# Images for community stylesheets. The objects stay in storage when a row goes: old stylesheet
# revisions may still name them.
community_images = Table(
    'community_images',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('name', Text, primary_key=True),
    Column('key', Text, nullable=False),
    Column('width', Integer, nullable=False),
    Column('height', Integer, nullable=False),
    Column('bytes', Integer, nullable=False),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# The stylesheet as served, compiled from the config/stylesheet wiki page.
community_stylesheets = Table(
    'community_stylesheets',
    metadata,
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('css', Text, nullable=False),
    Column('updated', DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# r2's labeled multis: a user's named set of communities, read as one listing.
multis = Table(
    'multis',
    metadata,
    Column('id', BigInteger, Identity(), primary_key=True),
    Column('owner_id', BigInteger, ForeignKey('accounts.id'), nullable=False),
    # As in the path, /user/<owner>/m/<name>; unique per owner whatever its case.
    Column('name', Text, nullable=False),
    Column('display_name', Text, nullable=False, server_default=''),
    Column('description_md', Text, nullable=False, server_default=''),
    Column('description_html', Text, nullable=False, server_default=''),
    # private: the owner only; public: anyone, and listed; hidden: anyone with the link.
    Column('visibility', Text, nullable=False, server_default='private'),
    Column('key_color', Text, nullable=False, server_default=''),
    Column('weighting_scheme', Text, nullable=False, server_default='classic'),
    Column('copied_from', Text),
    Column('created', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("visibility in ('private', 'public', 'hidden')", name='visibility'),
    Index('uq_multis_owner_name', 'owner_id', text('lower(name)'), unique=True),
)

multi_communities = Table(
    'multi_communities',
    metadata,
    Column('multi_id', BigInteger, ForeignKey('multis.id', ondelete='CASCADE'), primary_key=True),
    Column('community_id', BigInteger, ForeignKey('communities.id'), primary_key=True),
    Column('added', DateTime(timezone=True), nullable=False, server_default=func.now()),
)
