"""OAuth2 for API apps: letting an app in, its tokens, and the scope list.

r2's /api/v1/authorize, /api/v1/access_token, /api/v1/revoke_token and /api/v1/scopes. The token
endpoints take the app's id and secret with HTTP Basic, or as ``client_id`` and
``client_secret`` in the form, and answer in RFC 6749's JSON.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from litestar import Request, Response, get, post
from litestar.params import FromQuery
from litestar.response import Redirect
from litestar.status_codes import HTTP_200_OK, HTTP_204_NO_CONTENT, HTTP_303_SEE_OTHER

from app.controllers.auth import CSRF_EXEMPT, to_viewer
from app.controllers.deps import AppsDep, RedisDep, UserDep
from app.controllers.render import CONTENT_SECURITY_POLICY, Rendered
from app.services import oauth
from app.services.oauth import OAuthError, Problem, UnknownAppError
from app.services.sessions import Authenticated
from app.views.oauth import AuthorizePage, ScopesJson

AUTHORIZE = '/api/v1/authorize'
# The authorization request's parameters, carried from the page to the answer.
REQUEST_PARAMS = (
    'client_id',
    'response_type',
    'state',
    'redirect_uri',
    'duration',
    'scope',
    'code_challenge',
    'code_challenge_method',
)
NO_STORE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}


def _with_params(uri: str, **params: str) -> str:
    parts = urlsplit(uri)
    query = '&'.join(part for part in (parts.query, urlencode(params)) if part)
    return urlunsplit(parts._replace(query=query))


def _send_back(error: OAuthError) -> Redirect:
    params = {'error': str(error.error)}
    if error.state:
        params['state'] = error.state
    return Redirect(
        _with_params(error.redirect_uri or '', **params), status_code=HTTP_303_SEE_OTHER
    )


def _form_action(redirect_uri: str) -> str:
    """The page's CSP with the app's address added to ``form-action``.

    Browsers hold a form's redirect to the page's ``form-action``, and the answer redirects to
    the app.
    """
    parts = urlsplit(redirect_uri)
    target = f'{parts.scheme}://{parts.netloc}' if parts.netloc else f'{parts.scheme}:'
    return CONTENT_SECURITY_POLICY.replace("form-action 'self'", f"form-action 'self' {target}")


async def _params(request: Request[Any, Any, Any]) -> dict[str, str]:
    source = request.query_params if request.method == 'GET' else await request.form()
    return {
        name: value
        for name in REQUEST_PARAMS
        if isinstance(value := source.get(name), str) and value
    }


def _problem(user: Authenticated, error: UnknownAppError) -> Rendered:
    return Rendered(content=AuthorizePage(viewer=to_viewer(user), problem=str(error)))


@get(AUTHORIZE)
async def show_authorize(
    request: Request[Any, Any, Any], user: UserDep, apps: AppsDep
) -> Rendered | Redirect:
    params = await _params(request)
    try:
        asked = await oauth.check_request(apps, params)
    except UnknownAppError as error:
        return _problem(user, error)
    except OAuthError as error:
        return _send_back(error)
    page = AuthorizePage(
        viewer=to_viewer(user),
        client=asked.client,
        scopes=tuple(scope for scope in oauth.SCOPES.values() if scope.id in asked.scopes),
        permanent=asked.duration == 'permanent',
        params=params,
    )
    headers = {'Content-Security-Policy': _form_action(asked.redirect_uri)}
    return Rendered(content=page, headers=headers)


@post(AUTHORIZE)
async def answer_authorize(
    request: Request[Any, Any, Any], user: UserDep, apps: AppsDep, redis: RedisDep
) -> Rendered | Redirect:
    params = await _params(request)
    try:
        asked = await oauth.check_request(apps, params)
    except UnknownAppError as error:
        return _problem(user, error)
    except OAuthError as error:
        return _send_back(error)
    if (await request.form()).get('authorize') != 'yes':
        declined = OAuthError(Problem.ACCESS_DENIED)
        declined.redirect_uri, declined.state = asked.redirect_uri, asked.state
        return _send_back(declined)
    code = await oauth.issue_code(redis, asked, user.account)
    back = {'code': code, **({'state': asked.state} if asked.state else {})}
    return Redirect(_with_params(asked.redirect_uri, **back), status_code=HTTP_303_SEE_OTHER)


def _credentials(request: Request[Any, Any, Any], form: dict[str, str]) -> tuple[str, str]:
    """The app's id and secret: HTTP Basic, form-encoded as RFC 6749 says, or the form."""
    scheme, _, encoded = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'basic':
        return form.get('client_id', ''), form.get('client_secret', '')
    try:
        decoded = base64.b64decode(encoded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        raise OAuthError(Problem.INVALID_CLIENT, status_code=oauth.UNAUTHORIZED) from None
    client_id, _, secret = decoded.partition(':')
    return unquote_plus(client_id), unquote_plus(secret)


async def _form(request: Request[Any, Any, Any]) -> dict[str, str]:
    return {name: value for name, value in (await request.form()).items() if isinstance(value, str)}


@post('/api/v1/access_token', opt={CSRF_EXEMPT: True})
async def access_token(
    request: Request[Any, Any, Any], apps: AppsDep, redis: RedisDep
) -> Response[Any]:
    form = await _form(request)
    client = await oauth.authenticate(apps, *_credentials(request, form))
    match form.get('grant_type'):
        case 'authorization_code':
            issued = await oauth.exchange_code(
                redis,
                apps,
                client,
                code=form.get('code', ''),
                redirect_uri=form.get('redirect_uri', ''),
                code_verifier=form.get('code_verifier', ''),
            )
        case 'refresh_token':
            issued = await oauth.refresh(apps, client, form.get('refresh_token', ''))
        case 'client_credentials':
            issued = await oauth.client_credentials(apps, client, form.get('scope', ''))
        case _:
            raise OAuthError(Problem.UNSUPPORTED_GRANT_TYPE)
    return Response(content=issued.to_json(), status_code=HTTP_200_OK, headers=NO_STORE)


@post('/api/v1/revoke_token', opt={CSRF_EXEMPT: True})
async def revoke_token(request: Request[Any, Any, Any], apps: AppsDep) -> Response[None]:
    form = await _form(request)
    client = await oauth.authenticate(apps, *_credentials(request, form))
    await oauth.revoke(apps, client, form.get('token', ''))
    return Response(content=None, status_code=HTTP_204_NO_CONTENT)


@get('/api/v1/scopes')
async def show_scopes(scopes: FromQuery[str] = '') -> ScopesJson:
    """Every scope, or those named in ``scopes``."""
    wanted = frozenset(scopes.replace(',', ' ').split())
    shown = tuple(s for s in oauth.SCOPES.values() if not wanted or s.id in wanted)
    return ScopesJson(scopes=shown)
