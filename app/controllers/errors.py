"""Exception handlers: errors become an error page or reddit's error list, from one place."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from litestar import Response
from litestar.exceptions import NotFoundException
from litestar.status_codes import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND

from app.controllers.auth import viewer_from_state
from app.controllers.render import Rendered, wants_json
from app.errors import ErrorCode, FieldError, InaError
from app.services.oauth import OAuthError, Problem
from app.views.errors import ErrorPage

if TYPE_CHECKING:
    from litestar import Request


def handle_ina_error(request: Request[Any, Any, Any], error: InaError) -> Response[Any]:
    needs_login = any(e.code is ErrorCode.USER_REQUIRED for e in error.errors)
    if needs_login and request.method == 'GET' and not wants_json(request):
        dest = request.url.path + (f'?{request.url.query}' if request.url.query else '')
        location = '/login?' + urlencode({'dest': dest})
        return Response(content=b'', status_code=HTTP_303_SEE_OTHER, headers={'Location': location})
    page = ErrorPage(
        viewer=viewer_from_state(request), errors=error.errors, status=error.status_code
    )
    return Rendered(content=page)


def handle_not_found(request: Request[Any, Any, Any], _: NotFoundException) -> Response[Any]:
    page = ErrorPage(
        viewer=viewer_from_state(request),
        errors=(FieldError(ErrorCode.NOT_FOUND),),
        status=HTTP_404_NOT_FOUND,
    )
    return Rendered(content=page)


def handle_oauth_error(_: Request[Any, Any, Any], error: OAuthError) -> Response[Any]:
    """RFC 6749's error body, and how to authenticate (RFC 6750's challenge, for tokens)."""
    headers = {'Cache-Control': 'no-store'}
    if error.error is Problem.INVALID_CLIENT:
        headers['WWW-Authenticate'] = 'Basic realm="ina"'
    elif error.error in {Problem.INVALID_TOKEN, Problem.INSUFFICIENT_SCOPE}:
        challenge = f'Bearer realm="ina", error="{error.error}"'
        headers['WWW-Authenticate'] = challenge + (
            f', scope="{error.scope}"' if error.scope else ''
        )
    return Response(content=error.to_json(), status_code=error.status_code, headers=headers)
