"""Which OAuth scope each route needs, as r2 had them but for communities.

A route missing here is closed to API apps: signing in and out, passwords, the settings and
form pages, and the OAuth endpoints themselves. Two routes need a scope that depends on what
they are asked: ``/api/friend`` and ``/api/unfriend`` by the relation's type, and a
community's ``about`` pages by which one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from litestar import Request

from app.controllers import (
    api_docs,
    comments,
    communities,
    flair,
    listings,
    marks,
    messages,
    moderation,
    multis,
    oauth,
    prefs,
    relations,
    search,
    stylesheets,
    users,
    wiki,
)
from app.controllers.auth import ANY_TOKEN, SCOPE
from app.services.relations import ACCOUNT_RELATIONS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from litestar.handlers import HTTPRouteHandler

# The relations an innkeeper keeps for a community, by the scope that changes them.
MOD_OTHERS = frozenset({'moderator', 'moderator_invite'})


async def relation_scope(request: Request[Any, Any, Any]) -> str:
    kind = (await request.form()).get('type')
    if kind in ACCOUNT_RELATIONS:
        return 'subscribe'
    return 'modothers' if kind in MOD_OTHERS else 'modcontributors'


async def about_scope(request: Request[Any, Any, Any]) -> str:
    return 'modlog' if request.path_params.get('where') == 'log' else 'read'


type Needed = str | Callable[[Request[Any, Any, Any]], Awaitable[str]]

ROUTES: dict[str, tuple[HTTPRouteHandler, ...]] = {
    ANY_TOKEN: (oauth.show_scopes, api_docs.show_openapi),
    'identity': (users.show_identity, users.show_me, prefs.show_prefs_json),
    'read': (
        listings.show_front,
        listings.show_community,
        listings.show_community_sorted,
        listings.show_domain,
        listings.show_friends_links,
        listings.random_community,
        comments.show_link,
        comments.show_comment,
        comments.show_duplicates,
        comments.more_children,
        communities.show_community_about,
        communities.show_popular,
        communities.show_newest,
        search.show_search,
        search.show_community_search,
        search.show_community_results,
        search.get_community_names,
        search.post_community_names,
        multis.show_multi,
        multis.show_my_multi,
        multis.my_multis,
        multis.user_multis,
        multis.get_multi,
        multis.get_multi_community,
        users.show_user_about,
        users.redirect_short_user,
        users.redirect_short_user_path,
        relations.show_friends,
        relations.show_blocked,
    ),
    'history': (users.show_user, users.show_own_user),
    'vote': (comments.vote,),
    'submit': (listings.submit, comments.comment),
    'edit': (comments.edit_usertext, comments.delete_thing),
    'save': (marks.save, marks.unsave),
    'report': (moderation.report, marks.hide, marks.unhide),
    'subscribe': (
        communities.subscribe,
        multis.create_multi,
        multis.put_multi,
        multis.delete_multi,
        multis.copy_multi,
        multis.add_multi_community,
        multis.remove_multi_community,
    ),
    'mycommunities': (communities.show_mine, communities.show_moderated),
    'privatemessages': (
        messages.compose,
        messages.read_message,
        messages.unread_message,
        messages.read_all_messages,
        messages.show_box,
        messages.show_messages_box,
        messages.show_conversation,
        relations.block,
    ),
    'modposts': (
        moderation.remove,
        moderation.approve,
        moderation.ignore_reports,
        moderation.distinguish,
        moderation.set_sticky,
        moderation.lock,
        moderation.mark_nsfw,
    ),
    'modconfig': (
        communities.site_admin,
        communities.show_community_edit,
        communities.show_traffic,
        moderation.show_bouncer,
        stylesheets.show_stylesheet,
        stylesheets.save_stylesheet,
        stylesheets.upload_image,
        stylesheets.delete_image,
        stylesheets.delete_header,
    ),
    'modflair': (
        flair.show_flair,
        flair.set_flair,
        flair.delete_flair,
        flair.flair_csv,
        flair.flair_list,
        flair.flair_config,
        flair.flair_template,
        flair.delete_flair_template,
        flair.clear_flair_templates,
    ),
    'flair': (flair.flair_selector, flair.select_flair, flair.set_flair_enabled),
    'modself': (moderation.accept_invite, moderation.leave_moderator),
    'setpermissions': (moderation.set_permissions,),
    'wikiread': (wiki.show_wiki,),
    'wikiedit': (wiki.edit,),
    'modwiki': (wiki.revert, wiki.hide, wiki.allow_editor, wiki.save_settings),
}
DYNAMIC: dict[HTTPRouteHandler, Needed] = {
    moderation.friend: relation_scope,
    moderation.unfriend: relation_scope,
    moderation.show_about: about_scope,
}


def needed() -> dict[HTTPRouteHandler, Needed]:
    """Each route's scope."""
    found: dict[HTTPRouteHandler, Needed] = {
        handler: scope for scope, handlers in ROUTES.items() for handler in handlers
    }
    return {**found, **DYNAMIC}


def apply(handlers: Iterable[HTTPRouteHandler]) -> None:
    """Put each route's scope on its ``opt``, where the bearer guard looks for it."""
    scopes = needed()
    for handler in handlers:
        if handler in scopes:
            handler.opt[SCOPE] = scopes[handler]
