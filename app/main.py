"""The Litestar app factory."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

from litestar import Litestar
from litestar.datastructures import State
from litestar.di import Provide
from litestar.enums import ScopeType
from litestar.exceptions import NotFoundException
from litestar.plugins.jinja import JinjaTemplateEngine
from litestar.static_files import create_static_files_router
from litestar.template import TemplateConfig
from redis.asyncio import Redis

from app.controllers import (
    accounts,
    admin,
    admin_bar,
    api_docs,
    apps,
    assets,
    comments,
    communities,
    flair,
    gates,
    health,
    listings,
    marks,
    messages,
    moderation,
    multis,
    oauth,
    prefs,
    relations,
    scopes,
    search,
    stylesheets,
    users,
    wiki,
)
from app.controllers.admin_bar import admin_bar_middleware
from app.controllers.auth import bearer_guard, csrf_guard, provide_user, provide_viewer
from app.controllers.consent import Interstitial, handle_interstitial
from app.controllers.deps import (
    provide_accounts,
    provide_admin_log,
    provide_apps,
    provide_comments,
    provide_communities,
    provide_details,
    provide_flair,
    provide_links,
    provide_marks,
    provide_messages,
    provide_moderation,
    provide_multis,
    provide_stylesheets,
    provide_subscriptions,
    provide_votes,
    provide_wiki,
)
from app.controllers.errors import handle_ina_error, handle_not_found, handle_oauth_error
from app.controllers.render import (
    Rendered,
    api_type_guard,
    extra_headers_middleware,
    strip_json_suffix,
)
from app.errors import InaError
from app.infra import db, timing
from app.infra.assets import Assets
from app.infra.cache import Cache
from app.infra.opensearch import SearchClient
from app.infra.redis import create_redis
from app.infra.storage import ObjectStore, StorageError, create_store
from app.search import create_search
from app.services.oauth import OAuthError
from app.services.passwords import Passwords
from app.settings import Settings, get_settings
from app.views import format as filters

if TYPE_CHECKING:
    from litestar.types import (
        HTTPScope,
        LifeSpanReceive,
        LifeSpanScope,
        LifeSpanSend,
        Receive,
        Scope,
        Send,
    )

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / 'templates'
# In production the edge serves these; locally the app does.
STATIC = Path(__file__).parent.parent / 'static'

ROUTES = [
    health.show_health,
    assets.show_asset,
    wiki.show_wiki,
    wiki.edit,
    wiki.revert,
    wiki.hide,
    wiki.allow_editor,
    wiki.save_settings,
    flair.show_flair,
    flair.set_flair,
    flair.delete_flair,
    flair.flair_csv,
    flair.flair_list,
    flair.flair_config,
    flair.flair_template,
    flair.delete_flair_template,
    flair.clear_flair_templates,
    flair.flair_selector,
    flair.select_flair,
    flair.set_flair_enabled,
    stylesheets.show_stylesheet,
    stylesheets.save_stylesheet,
    stylesheets.upload_image,
    stylesheets.delete_image,
    stylesheets.delete_header,
    stylesheets.serve_stylesheet,
    stylesheets.serve_media,
    multis.show_multi,
    multis.show_my_multi,
    multis.edit_multi,
    multis.my_multis,
    multis.user_multis,
    multis.get_multi,
    multis.create_multi,
    multis.put_multi,
    multis.delete_multi,
    multis.create_multi_from_form,
    multis.copy_multi,
    multis.get_multi_community,
    multis.add_multi_community,
    multis.remove_multi_community,
    search.show_search,
    search.show_community_search,
    search.show_community_results,
    search.get_community_names,
    search.post_community_names,
    accounts.show_login,
    accounts.login,
    accounts.register,
    accounts.logout,
    accounts.show_password,
    accounts.request_password_reset,
    accounts.show_reset_password,
    accounts.reset_password,
    prefs.show_prefs,
    prefs.update_prefs,
    prefs.show_prefs_update,
    prefs.update_password,
    prefs.update_email,
    prefs.show_prefs_json,
    apps.show_apps,
    apps.update_app,
    apps.new_app_secret,
    apps.delete_app,
    apps.revoke_app,
    oauth.show_authorize,
    oauth.answer_authorize,
    oauth.access_token,
    oauth.revoke_token,
    oauth.show_scopes,
    api_docs.show_api,
    admin.show_admin,
    admin_bar.show_timings,
    admin.show_admin_on,
    admin.admin_on,
    admin.admin_off,
    admin.suspend,
    admin.unsuspend,
    admin.act_on_community,
    admin.remove_all,
    api_docs.show_openapi,
    users.show_user,
    users.show_user_about,
    users.show_own_user,
    users.redirect_short_user,
    users.redirect_short_user_path,
    users.show_identity,
    users.show_me,
    communities.show_traffic,
    communities.show_community_about,
    communities.show_community_edit,
    communities.show_community_create,
    communities.site_admin,
    communities.subscribe,
    communities.show_popular,
    communities.show_newest,
    communities.show_mine,
    communities.show_moderated,
    listings.show_front,
    listings.random_community,
    listings.show_friends_links,
    listings.show_domain,
    listings.show_community,
    listings.show_community_sorted,
    comments.show_link,
    comments.show_comment,
    comments.show_duplicates,
    comments.comment,
    comments.vote,
    comments.edit_usertext,
    comments.delete_thing,
    gates.show_over18,
    gates.answer_over18,
    gates.show_quarantine,
    gates.answer_quarantine,
    relations.show_friends,
    relations.show_blocked,
    relations.block,
    marks.save,
    marks.unsave,
    marks.hide,
    marks.unhide,
    comments.more_children,
    messages.show_compose,
    messages.compose,
    messages.show_box,
    messages.show_messages_box,
    messages.show_conversation,
    messages.read_message,
    messages.unread_message,
    messages.read_all_messages,
    moderation.show_about,
    moderation.show_bouncer,
    moderation.remove,
    moderation.approve,
    moderation.ignore_reports,
    moderation.distinguish,
    moderation.set_sticky,
    moderation.lock,
    moderation.mark_nsfw,
    moderation.report,
    moderation.friend,
    moderation.unfriend,
    moderation.set_permissions,
    moderation.accept_invite,
    moderation.leave_moderator,
    listings.show_submit,
    listings.submit,
]


class App(Litestar):
    @override
    async def __call__(
        self,
        scope: Scope | LifeSpanScope,
        receive: Receive | LifeSpanReceive,
        send: Send | LifeSpanSend,
    ) -> None:
        if scope['type'] == ScopeType.HTTP:
            strip_json_suffix(cast('HTTPScope', scope))
        await super().__call__(scope, receive, send)


def create_app(settings: Settings | None = None) -> Litestar:
    settings = settings or get_settings()
    engine = db.create_engine(settings)
    timing.record_statements(engine)
    redis = create_redis(settings)
    search_client = create_search(settings)
    store = create_store(settings)
    bundle = Assets(STATIC / 'css')
    passwords = Passwords(settings)

    def provide_settings() -> Settings:
        return settings

    def provide_redis() -> Redis:
        return redis

    def provide_passwords() -> Passwords:
        return passwords

    def provide_search() -> SearchClient:
        return search_client

    def provide_store() -> ObjectStore:
        return store

    async def prepare_store() -> None:
        # A missing bucket is made here; without storage the site still runs, minus uploads.
        try:
            await store.ensure_bucket()
        except StorageError:
            log.warning('object storage is not reachable; uploads will fail', exc_info=True)

    cache = Cache(redis)
    scopes.apply(ROUTES)

    def provide_cache() -> Cache:
        return cache

    return App(
        route_handlers=[*ROUTES, create_static_files_router(path='/static', directories=[STATIC])],
        dependencies={
            'settings': Provide(provide_settings, sync_to_thread=False),
            'db_session': Provide(db.provide_db_session, sync_to_thread=False),
            'redis': Provide(provide_redis, sync_to_thread=False),
            'passwords': Provide(provide_passwords, sync_to_thread=False),
            'accounts': Provide(provide_accounts, sync_to_thread=False),
            'admin_log': Provide(provide_admin_log, sync_to_thread=False),
            'communities': Provide(provide_communities, sync_to_thread=False),
            'subscriptions': Provide(provide_subscriptions, sync_to_thread=False),
            'links': Provide(provide_links, sync_to_thread=False),
            'votes': Provide(provide_votes, sync_to_thread=False),
            'comments': Provide(provide_comments, sync_to_thread=False),
            'messages': Provide(provide_messages, sync_to_thread=False),
            'moderation': Provide(provide_moderation, sync_to_thread=False),
            'wiki': Provide(provide_wiki, sync_to_thread=False),
            'flair': Provide(provide_flair, sync_to_thread=False),
            'stylesheets': Provide(provide_stylesheets, sync_to_thread=False),
            'multis': Provide(provide_multis, sync_to_thread=False),
            'marks': Provide(provide_marks, sync_to_thread=False),
            'apps': Provide(provide_apps, sync_to_thread=False),
            'details': Provide(provide_details, sync_to_thread=False),
            'store': Provide(provide_store, sync_to_thread=False),
            'cache': Provide(provide_cache, sync_to_thread=False),
            'search': Provide(provide_search, sync_to_thread=False),
            'viewer': Provide(provide_viewer),
            'user': Provide(provide_user),
        },
        middleware=[
            extra_headers_middleware,
            admin_bar_middleware(settings, redis),
            db.transaction_middleware(engine),
        ],
        guards=[api_type_guard, bearer_guard, csrf_guard],
        exception_handlers={
            InaError: handle_ina_error,
            OAuthError: handle_oauth_error,
            NotFoundException: handle_not_found,
            Interstitial: handle_interstitial,
        },
        response_class=Rendered,
        template_config=TemplateConfig(
            directory=TEMPLATES,
            engine=JinjaTemplateEngine,
            engine_callback=lambda engine: filters.register(
                engine.engine, bundle, settings.media_url, settings.source_url, debug=settings.debug
            ),
        ),
        state=State({'settings': settings, 'assets': bundle, 'redis': redis}),
        on_startup=[prepare_store],
        on_shutdown=[engine.dispose, redis.aclose, search_client.aclose, store.aclose],
        # The API's OpenAPI document is built from the routes and their scopes, at
        # /dev/api/openapi; Litestar's own would describe views, not the JSON they answer with.
        openapi_config=None,
        debug=settings.debug,
    )
