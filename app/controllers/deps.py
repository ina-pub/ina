"""Annotations for injected dependencies, and the providers for the repositories.

Each annotation is a named dependency that Litestar passes through without validating: these are
trusted objects built by the app, and converting them on every request would only cost time.
"""

from __future__ import annotations

from typing import TypeAlias

from litestar.di import NamedDependency
from litestar.params import SkipValidation
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.details import Details
from app.infra.cache import Cache
from app.infra.opensearch import SearchClient
from app.infra.storage import ObjectStore
from app.repos.base import (
    AccountRepo,
    AdminRepo,
    CommentRepo,
    CommunityRepo,
    FlairRepo,
    LinkRepo,
    MarkRepo,
    MessageRepo,
    ModerationRepo,
    MultiRepo,
    OAuthRepo,
    StylesheetRepo,
    SubscriptionRepo,
    VoteRepo,
    WikiRepo,
)
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.admin import PgAdminRepo
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.flair import PgFlairRepo
from app.repos.pg.links import PgLinkRepo, PgVoteRepo
from app.repos.pg.marks import PgMarkRepo
from app.repos.pg.messages import PgMessageRepo
from app.repos.pg.moderation import PgModerationRepo
from app.repos.pg.multis import PgMultiRepo
from app.repos.pg.oauth import PgOAuthRepo
from app.repos.pg.stylesheets import PgStylesheetRepo
from app.repos.pg.subscriptions import PgSubscriptionRepo
from app.repos.pg.wiki import PgWikiRepo
from app.services.passwords import Passwords
from app.services.sessions import Authenticated
from app.settings import Settings

SettingsDep: TypeAlias = NamedDependency[SkipValidation[Settings]]
DbSessionDep: TypeAlias = NamedDependency[SkipValidation[AsyncSession]]
RedisDep: TypeAlias = NamedDependency[SkipValidation[Redis]]
PasswordsDep: TypeAlias = NamedDependency[SkipValidation[Passwords]]
AccountsDep: TypeAlias = NamedDependency[SkipValidation[AccountRepo]]
AdminLogDep: TypeAlias = NamedDependency[SkipValidation[AdminRepo]]
CacheDep: TypeAlias = NamedDependency[SkipValidation[Cache]]
SearchDep: TypeAlias = NamedDependency[SkipValidation[SearchClient]]
CommentsDep: TypeAlias = NamedDependency[SkipValidation[CommentRepo]]
ModerationDep: TypeAlias = NamedDependency[SkipValidation[ModerationRepo]]
MessagesDep: TypeAlias = NamedDependency[SkipValidation[MessageRepo]]
LinksDep: TypeAlias = NamedDependency[SkipValidation[LinkRepo]]
VotesDep: TypeAlias = NamedDependency[SkipValidation[VoteRepo]]
CommunitiesDep: TypeAlias = NamedDependency[SkipValidation[CommunityRepo]]
SubscriptionsDep: TypeAlias = NamedDependency[SkipValidation[SubscriptionRepo]]
WikiDep: TypeAlias = NamedDependency[SkipValidation[WikiRepo]]
FlairDep: TypeAlias = NamedDependency[SkipValidation[FlairRepo]]
StylesheetsDep: TypeAlias = NamedDependency[SkipValidation[StylesheetRepo]]
StoreDep: TypeAlias = NamedDependency[SkipValidation[ObjectStore]]
MultisDep: TypeAlias = NamedDependency[SkipValidation[MultiRepo]]
MarksDep: TypeAlias = NamedDependency[SkipValidation[MarkRepo]]
AppsDep: TypeAlias = NamedDependency[SkipValidation[OAuthRepo]]
DetailsDep: TypeAlias = NamedDependency[SkipValidation[Details]]
# The signed-in account, or None.
ViewerDep: TypeAlias = NamedDependency[SkipValidation[Authenticated | None]]
# The signed-in account; USER_REQUIRED if there is none.
UserDep: TypeAlias = NamedDependency[SkipValidation[Authenticated]]


def provide_accounts(db_session: DbSessionDep) -> AccountRepo:
    return PgAccountRepo(db_session)


def provide_admin_log(db_session: DbSessionDep) -> AdminRepo:
    return PgAdminRepo(db_session)


def provide_communities(db_session: DbSessionDep) -> CommunityRepo:
    return PgCommunityRepo(db_session)


def provide_subscriptions(db_session: DbSessionDep) -> SubscriptionRepo:
    return PgSubscriptionRepo(db_session)


def provide_links(db_session: DbSessionDep) -> LinkRepo:
    return PgLinkRepo(db_session)


def provide_votes(db_session: DbSessionDep) -> VoteRepo:
    return PgVoteRepo(db_session)


def provide_comments(db_session: DbSessionDep) -> CommentRepo:
    return PgCommentRepo(db_session)


def provide_messages(db_session: DbSessionDep) -> MessageRepo:
    return PgMessageRepo(db_session)


def provide_moderation(db_session: DbSessionDep) -> ModerationRepo:
    return PgModerationRepo(db_session)


def provide_wiki(db_session: DbSessionDep) -> WikiRepo:
    return PgWikiRepo(db_session)


def provide_flair(db_session: DbSessionDep) -> FlairRepo:
    return PgFlairRepo(db_session)


def provide_stylesheets(db_session: DbSessionDep) -> StylesheetRepo:
    return PgStylesheetRepo(db_session)


def provide_multis(db_session: DbSessionDep) -> MultiRepo:
    return PgMultiRepo(db_session)


def provide_marks(db_session: DbSessionDep) -> MarkRepo:
    return PgMarkRepo(db_session)


def provide_apps(db_session: DbSessionDep) -> OAuthRepo:
    return PgOAuthRepo(db_session)


def provide_details(
    flair: FlairDep,
    communities: CommunitiesDep,
    marks: MarksDep,
    accounts: AccountsDep,
    settings: SettingsDep,
) -> Details:
    return Details(flair, communities, marks, accounts, settings.media_url)
