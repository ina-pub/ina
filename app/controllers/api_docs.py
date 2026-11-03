"""``/dev/api``: the API's reference, and the same as an OpenAPI 3.1 document.

Both are built from the app's own routes: each route an app may call (it has a scope), with its
path and query parameters, its form fields and its scope, and a summary written here for the
people writing apps (the docstrings are for the people writing ina). The kinds' fields come from
app.views.shapes, which the shape test holds the API to.
"""

from __future__ import annotations

import dataclasses
import re
import types
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin, get_type_hints

from litestar import Request, get
from litestar.params import BodyKwarg, QueryParameter

from app.controllers import (
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
from app.controllers.auth import ANY_TOKEN, SCOPE, to_viewer
from app.controllers.deps import SettingsDep, ViewerDep
from app.controllers.render import Rendered
from app.controllers.search import NamesForm
from app.services.oauth import APP_ONLY_SCOPES, SCOPES
from app.views.accounts import Identity, Me, ProfileAbout, ProfilePage
from app.views.api_docs import ApiPage, Endpoint, OpenApiJson
from app.views.communities import CommunitiesPage, CommunityAbout
from app.views.links import CommunityListingPage, FrontPage
from app.views.messages import MessagesPage
from app.views.multis import MultiPage
from app.views.search import SearchPage
from app.views.shapes import KINDS

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.handlers import HTTPRouteHandler

    from app.jsontypes import JSON

DOCUMENT = 'openapi_document'
PATH_PARAM = re.compile(r'\{(\w+):\w+\}')
# What a route's view answers in JSON, by the schema below that describes it.
ANSWERS: dict[type, str] = {
    FrontPage: 'Listing',
    CommunityListingPage: 'Listing',
    MultiPage: 'Listing',
    SearchPage: 'Listing',
    CommunitiesPage: 'Listing',
    ProfilePage: 'Listing',
    MessagesPage: 'Listing',
    CommunityAbout: 'Thing',
    ProfileAbout: 'Thing',
    Me: 'Thing',
    Identity: 'Account',
}
TYPES = {str: 'string', int: 'integer', float: 'number', bool: 'boolean'}
# Routes that read their query by hand, with the form whose fields it holds.
# By function, as BY_FUNCTION below is.
QUERY_FORMS: dict[object, type[Any]] = {search.get_community_names.fn: NamesForm}
# The scopes a route picking its own may need, by how it picks.
CHOICES = {
    'relation_scope': ('subscribe', 'modcontributors', 'modothers'),
    'about_scope': ('read', 'modlog'),
}


def _schema(annotation: Any) -> dict[str, JSON]:  # noqa: ANN401
    """A JSON schema for a parameter's type: a string, number or boolean, maybe null."""
    if get_origin(annotation) in {Union, types.UnionType}:
        options = [arg for arg in get_args(annotation) if arg is not type(None)]
        schema = _schema(options[0]) if len(options) == 1 else {}
        kind = schema.get('type')
        return {**schema, 'type': [kind, 'null']} if isinstance(kind, str) else schema
    return {'type': TYPES[annotation]} if annotation in TYPES else {'type': 'string'}


def _parameters(path: str, handler: HTTPRouteHandler) -> list[JSON]:
    found: list[JSON] = [
        {'name': name, 'in': 'path', 'required': True, 'schema': {'type': 'string'}}
        for name in PATH_PARAM.findall(path)
    ]
    if handler.fn in QUERY_FORMS:
        found.extend(
            {'name': name, 'in': 'query', 'required': False, 'schema': schema}
            for name, schema in _fields(QUERY_FORMS[handler.fn]).items()
        )
    for name, field in handler.parsed_fn_signature.parameters.items():
        kwarg = field.kwarg_definition
        if isinstance(kwarg, QueryParameter):
            found.append(
                {
                    'name': kwarg.query or name,
                    'in': 'query',
                    'required': not field.has_default,
                    'schema': _schema(field.annotation),
                }
            )
    return found


def _fields(form: type[Any]) -> dict[str, JSON]:
    hints = get_type_hints(form)
    return {field.name: _schema(hints[field.name]) for field in dataclasses.fields(form)}


def _form(handler: HTTPRouteHandler) -> JSON | None:
    """The form a route reads, from its body dataclass."""
    for field in handler.parsed_fn_signature.parameters.values():
        form = field.annotation
        if (
            isinstance(field.kwarg_definition, BodyKwarg)
            and isinstance(form, type)
            and dataclasses.is_dataclass(form)
        ):
            schema: JSON = {'type': 'object', 'properties': _fields(form)}
            return {'content': {'application/x-www-form-urlencoded': {'schema': schema}}}
    return None


def _answer(handler: HTTPRouteHandler, method: str) -> JSON:
    ref: str | None
    if handler.fn in {comments.show_link.fn, comments.show_comment.fn}:
        ref = 'LinkAndComments'
    else:
        returned = handler.parsed_fn_signature.return_type.annotation
        options = get_args(returned) if get_origin(returned) in {Union, types.UnionType} else ()
        returned = options[0] if options else returned
        ref = ANSWERS.get(returned) if isinstance(returned, type) else None
        if ref is None and returned is Rendered and method != 'GET':
            ref = 'FormResponse'
    schema: JSON = {'$ref': f'#/components/schemas/{ref}'} if ref else {}
    return {'description': 'OK', 'content': {'application/json': {'schema': schema}}}


def _needs(handler: HTTPRouteHandler) -> tuple[str, ...]:
    needed = handler.opt[SCOPE]
    if callable(needed):
        return CHOICES[needed.__name__]
    return () if needed == ANY_TOKEN else (str(needed),)


def endpoints(app: Litestar) -> list[Endpoint]:
    """Every route an app may call, by path and method."""
    found: list[Endpoint] = []
    for route in app.routes:
        for handler in getattr(route, 'route_handlers', ()):
            if SCOPE not in handler.opt:
                continue
            path = PATH_PARAM.sub(r'{\1}', route.path)
            found.extend(
                Endpoint(
                    method=method,
                    path=path,
                    raw_path=route.path,
                    handler=handler,
                    scopes=_needs(handler),
                    summary=BY_FUNCTION[handler.fn],
                )
                for method in sorted(handler.http_methods)
                if method != 'HEAD'
            )
    return sorted(found, key=lambda e: (e.path, e.method))


def _operation(endpoint: Endpoint, operation_ids: set[str]) -> JSON:
    handler = endpoint.handler
    operation_id = handler.fn.__name__
    while operation_id in operation_ids:
        operation_id += '_'
    operation_ids.add(operation_id)
    security: list[JSON] = [{'oauth2': [scope]} for scope in endpoint.scopes] or [{'oauth2': []}]
    operation: dict[str, JSON] = {
        'operationId': operation_id,
        'summary': endpoint.summary,
        'tags': [handler.fn.__module__.rsplit('.', 1)[-1]],
        'security': security,
        'parameters': _parameters(endpoint.raw_path, handler),
        'responses': {
            '200': _answer(handler, endpoint.method),
            '401': {'$ref': '#/components/responses/BadToken'},
            '403': {'$ref': '#/components/responses/MissingScope'},
            '429': {'$ref': '#/components/responses/TooMany'},
        },
    }
    if len(endpoint.scopes) > 1:
        operation['x-scope-depends-on-request'] = True
    form = _form(handler)
    if form is not None and endpoint.method != 'GET':
        operation['requestBody'] = form
    return operation


def _thing(kind: str, fields: frozenset[str]) -> JSON:
    return {
        'type': 'object',
        'description': f'The data of a {kind}.',
        'properties': {name: {} for name in sorted(fields)},
    }


def _token_endpoints() -> dict[str, JSON]:
    form: JSON = {
        'type': 'object',
        'required': ['grant_type'],
        'properties': {
            'grant_type': {'enum': ['authorization_code', 'refresh_token', 'client_credentials']},
            'code': {'type': 'string'},
            'redirect_uri': {'type': 'string'},
            'code_verifier': {'type': 'string', 'description': 'PKCE; installed apps send it.'},
            'refresh_token': {'type': 'string'},
            'scope': {'type': 'string', 'description': 'For client_credentials.'},
        },
    }
    errors: JSON = {
        'description': 'RFC 6749 error',
        'content': {'application/json': {'schema': {'$ref': '#/components/schemas/OAuthError'}}},
    }
    basic: list[JSON] = [{'app': []}]
    return {
        '/api/v1/access_token': {
            'post': {
                'operationId': 'access_token',
                'summary': 'Get an access token',
                'tags': ['oauth'],
                'security': basic,
                'requestBody': {'content': {'application/x-www-form-urlencoded': {'schema': form}}},
                'responses': {
                    '200': {
                        'description': 'A token',
                        'content': {
                            'application/json': {
                                'schema': {'$ref': '#/components/schemas/TokenResponse'}
                            }
                        },
                    },
                    '400': errors,
                    '401': errors,
                },
            }
        },
        '/api/v1/revoke_token': {
            'post': {
                'operationId': 'revoke_token',
                'summary': 'End the grant behind an access or refresh token',
                'tags': ['oauth'],
                'security': basic,
                'requestBody': {
                    'content': {
                        'application/x-www-form-urlencoded': {
                            'schema': {
                                'type': 'object',
                                'required': ['token'],
                                'properties': {'token': {'type': 'string'}},
                            }
                        }
                    }
                },
                'responses': {'204': {'description': 'Done'}, '401': errors},
            }
        },
    }


def _components() -> JSON:
    def ref(name: str) -> JSON:
        return {'$ref': f'#/components/schemas/{name}'}

    nullable_string: JSON = {'type': ['string', 'null']}
    schemas: dict[str, JSON] = {
        'Thing': {
            'type': 'object',
            'required': ['kind', 'data'],
            'properties': {
                'kind': {'enum': ['t1', 't2', 't3', 't4', 't5', 'more']},
                'data': {'oneOf': [ref(name) for name in KINDS] + [ref('More')]},
            },
        },
        'Listing': {
            'type': 'object',
            'properties': {
                'kind': {'const': 'Listing'},
                'data': {
                    'type': 'object',
                    'properties': {
                        'after': nullable_string,
                        'before': nullable_string,
                        'dist': {'type': 'integer'},
                        'modhash': {'type': 'string'},
                        'children': {'type': 'array', 'items': ref('Thing')},
                    },
                },
            },
        },
        'LinkAndComments': {
            'type': 'array',
            'description': 'A listing holding the link, then a listing of its comments.',
            'prefixItems': [ref('Listing'), ref('Listing')],
        },
        'More': _thing(
            'more', frozenset({'count', 'name', 'id', 'parent_id', 'depth', 'children'})
        ),
        'FormResponse': {
            'type': 'object',
            'properties': {
                'json': {
                    'type': 'object',
                    'properties': {
                        'errors': {'type': 'array', 'items': ref('Error')},
                        'data': {'type': 'object'},
                    },
                }
            },
        },
        'Error': {
            'type': 'array',
            'description': 'An error code, its message, and the field it is about.',
            'prefixItems': [{'type': 'string'}, {'type': 'string'}, nullable_string],
        },
        'OAuthError': {
            'type': 'object',
            'properties': {'error': {'type': 'string'}, 'error_description': {'type': 'string'}},
        },
        'TokenResponse': {
            'type': 'object',
            'properties': {
                'access_token': {'type': 'string'},
                'token_type': {'const': 'bearer'},
                'expires_in': {'type': 'integer'},
                'scope': {'type': 'string'},
                'refresh_token': {'type': 'string'},
            },
        },
    }
    for name, (kind, fields) in KINDS.items():
        schemas[name] = _thing(kind, fields)
    every: JSON = {scope.id: scope.description for scope in SCOPES.values()}
    oauth_error: JSON = {'application/json': {'schema': ref('OAuthError')}}
    return {
        'schemas': schemas,
        'responses': {
            'BadToken': {'description': 'The token is unknown or expired', 'content': oauth_error},
            'MissingScope': {
                'description': "The token lacks the route's scope, or the route is closed to apps",
                'content': oauth_error,
            },
            'TooMany': {
                'description': 'Over the rate limit; wait as Retry-After says',
                'content': {'application/json': {'schema': ref('FormResponse')}},
            },
        },
        'securitySchemes': {
            'oauth2': {
                'type': 'oauth2',
                'flows': {
                    'authorizationCode': {
                        'authorizationUrl': '/api/v1/authorize',
                        'tokenUrl': '/api/v1/access_token',
                        'refreshUrl': '/api/v1/access_token',
                        'scopes': every,
                    },
                    'clientCredentials': {'tokenUrl': '/api/v1/access_token', 'scopes': every},
                },
                'description': (
                    'With client credentials, a script gets a token for its owner, with any '
                    'scopes; a web app gets one for itself, with '
                    f'{" or ".join(sorted(APP_ONLY_SCOPES))} only.'
                ),
            },
            'app': {'type': 'http', 'scheme': 'basic', 'description': 'client_id:client_secret'},
        },
    }


INTRO = """\
The API answers in JSON. A Listing holds things, each a kind (t1 comment, t2 account, t3 link, \
t4 message, t5 community) and its data; ids are fullnames such as t3_1z, and a listing pages \
with after and before.

An app signs in with OAuth2. Register one on /prefs/apps. Each route needs the scope shown; \
routes not listed here are closed to apps. A token's requests need no modhash.

Each app may make 60 requests a minute for each account it acts for; every answer to a token \
says X-Ratelimit-Used, X-Ratelimit-Remaining and X-Ratelimit-Reset (seconds)."""


def document(app: Litestar, site_url: str, source_url: str) -> JSON:
    """The OpenAPI document, built once per app."""
    cached: JSON | None = app.state.get(DOCUMENT)
    if cached is not None:
        return cached
    paths: dict[str, dict[str, JSON]] = {}
    operation_ids: set[str] = set()
    for endpoint in endpoints(app):
        paths.setdefault(endpoint.path, {})[endpoint.method.lower()] = _operation(
            endpoint, operation_ids
        )
    built: JSON = {
        'openapi': '3.1.0',
        'info': {
            'title': 'ina',
            'version': '1',
            'description': INTRO,
            'license': {'name': 'AGPL-3.0-only', 'identifier': 'AGPL-3.0-only'},
        },
        'externalDocs': {'description': 'Source', 'url': source_url},
        'servers': [{'url': site_url}],
        'paths': {**paths, **_token_endpoints()},
        'components': _components(),
    }
    app.state[DOCUMENT] = built
    return built


@get('/dev/api')
async def show_api(request: Request[Any, Any, Any], viewer: ViewerDep) -> ApiPage:
    """What an app may call, by scope."""
    return ApiPage(viewer=to_viewer(viewer), endpoints=tuple(endpoints(request.app)))


@get('/dev/api/openapi')
async def show_openapi(request: Request[Any, Any, Any], settings: SettingsDep) -> OpenApiJson:
    """The API as an OpenAPI 3.1 document."""
    return OpenApiJson(document=document(request.app, settings.site_url, settings.source_url))


# What each route does, for the people writing apps.
SUMMARIES: dict[HTTPRouteHandler, str] = {
    listings.show_front: 'Links from the communities you are in, or popular ones',
    listings.show_community: "A community's links, hot first",
    listings.show_community_sorted: "A community's links, by a sort: hot, new, rising, top",
    listings.show_domain: 'Links to one domain',
    listings.show_friends_links: 'What your friends posted',
    listings.random_community: 'Redirect to a random community',
    listings.submit: 'Post a link or a text post',
    comments.show_link: 'A link and its comments',
    comments.show_comment: 'One comment and its replies, with context above',
    comments.show_duplicates: 'Other posts of the same URL',
    comments.more_children: 'Comments hidden behind "more replies"',
    comments.comment: 'Reply to a link, a comment or a message',
    comments.vote: 'Vote on a link or comment: 1, 0 or -1',
    comments.edit_usertext: 'Edit the text of your post or comment',
    comments.delete_thing: 'Delete your post or comment',
    marks.save: 'Save a link or comment',
    marks.unsave: 'Unsave a link or comment',
    marks.hide: 'Hide links from your listings',
    marks.unhide: 'Show hidden links again',
    communities.show_community_about: "A community's details",
    communities.show_popular: 'Communities, most members first',
    communities.show_newest: 'Communities, newest first',
    communities.show_mine: "Communities you're in",
    communities.show_moderated: 'Communities you keep',
    communities.subscribe: 'Join or leave communities',
    communities.site_admin: "Start a community, or change one's settings",
    communities.show_community_edit: "A community's settings",
    communities.show_traffic: "A community's traffic",
    search.show_search: 'Search links',
    search.show_community_search: 'Search links in a community',
    search.show_community_results: 'Search communities',
    search.get_community_names: 'Community names matching a query',
    search.post_community_names: 'Community names matching a query',
    users.show_identity: 'Your account',
    users.show_me: 'Your account, as a t2',
    users.show_user: 'What an account posted, or a tab of it: comments, submitted, saved...',
    users.show_own_user: 'Redirect to your profile',
    users.show_user_about: 'An account',
    users.redirect_short_user: 'Redirect u/name to its profile',
    users.redirect_short_user_path: 'Redirect u/name/... to its profile',
    prefs.show_prefs_json: 'Your preferences',
    relations.show_friends: 'Your friends',
    relations.show_blocked: "People you've blocked",
    relations.block: 'Block the author of a message, comment or link',
    messages.show_box: 'Your inbox, or a box of it: unread, sent, comments, mentions, modmail',
    messages.show_messages_box: 'Your private messages',
    messages.show_conversation: 'One conversation',
    messages.compose: 'Send a private message',
    messages.read_message: 'Mark messages read',
    messages.unread_message: 'Mark messages unread',
    messages.read_all_messages: 'Mark your whole inbox read',
    multis.show_multi: "A multi's links",
    multis.show_my_multi: 'Redirect to one of your multis',
    multis.my_multis: 'Your multis',
    multis.user_multis: "An account's public multis",
    multis.get_multi: 'A multi',
    multis.create_multi: 'Make a multi',
    multis.put_multi: 'Make or change a multi',
    multis.delete_multi: 'Delete a multi',
    multis.copy_multi: 'Copy a multi as a new one of yours',
    multis.get_multi_community: 'Whether a multi has a community',
    multis.add_multi_community: 'Add a community to a multi',
    multis.remove_multi_community: 'Take a community out of a multi',
    flair.show_flair: "A community's flair settings and who has flair",
    flair.flair_list: 'Who has flair in a community',
    flair.set_flair: "Set someone's flair, or a link's",
    flair.delete_flair: "Take away someone's flair",
    flair.flair_csv: 'Set many flairs at once, from CSV',
    flair.flair_config: "A community's flair options",
    flair.flair_template: 'Add or change a flair template',
    flair.delete_flair_template: 'Delete a flair template',
    flair.clear_flair_templates: 'Delete every flair template of a kind',
    flair.flair_selector: 'The flair you may choose, for you or a link',
    flair.select_flair: "Choose your flair, or a link's",
    flair.set_flair_enabled: 'Show or hide your flair in a community',
    stylesheets.show_stylesheet: "A community's stylesheet and images",
    stylesheets.save_stylesheet: "Change a community's stylesheet",
    stylesheets.upload_image: 'Upload an image for the stylesheet, or the header',
    stylesheets.delete_image: 'Delete a stylesheet image',
    stylesheets.delete_header: "Delete a community's header image",
    moderation.show_about: 'A moderation list: queue, reports, log, banned, innkeepers...',
    moderation.show_bouncer: "Redirect to the Bouncer's rules, on the wiki",
    moderation.remove: 'Remove a link or comment, or mark it spam',
    moderation.approve: 'Approve a link or comment',
    moderation.ignore_reports: 'Ignore or heed further reports on a link or comment',
    moderation.distinguish: 'Mark your post or comment as said by an innkeeper',
    moderation.set_sticky: 'Pin or unpin a link to the top of its community',
    moderation.lock: 'Lock or unlock a thread',
    moderation.mark_nsfw: 'Mark a link NSFW, or take the mark off; for its author or innkeepers',
    moderation.report: 'Report a link, comment or message to the innkeepers',
    moderation.friend: 'Add a friend, or ban, mute, approve or invite someone',
    moderation.unfriend: 'Undo a friend, ban, mute, approval or innkeeper',
    moderation.set_permissions: 'Change what an innkeeper may do',
    moderation.accept_invite: 'Accept an invitation to keep a community',
    moderation.leave_moderator: 'Stop keeping a community',
    wiki.show_wiki: 'A wiki page, its revisions, or the list of pages',
    wiki.edit: 'Edit a wiki page',
    wiki.revert: 'Revert a wiki page to a revision',
    wiki.hide: 'Hide or show a wiki revision',
    wiki.allow_editor: 'Let someone edit a wiki page, or stop them',
    wiki.save_settings: "Change a wiki page's settings",
    oauth.show_scopes: 'The scopes an app may ask for',
    show_openapi: 'This document',
}

# The app routes copies of the handlers, so they are looked up by function.
BY_FUNCTION = {handler.fn: text for handler, text in SUMMARIES.items()}
