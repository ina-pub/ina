"""Content negotiation: one handler, rendered as HTML or JSON depending on how it was asked for.

JSON is chosen by a ``.json`` path suffix, ``?api_type=json`` or ``Accept: application/json``;
everything else gets the view's Jinja2 template. Logged-out GETs may be cached at the edge for a
minute; anything with a session cookie is private.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from litestar import Request, Response
from litestar.connection import ASGIConnection
from litestar.enums import MediaType, RequestEncodingType, ScopeType
from litestar.handlers import BaseRouteHandler
from litestar.status_codes import HTTP_200_OK, HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND

from app.errors import ErrorCode, FieldError
from app.infra.timing import timed
from app.services import sessions
from app.views.base import View
from app.views.forms import FormResult

if TYPE_CHECKING:
    from collections.abc import Iterable

    from litestar import Litestar
    from litestar.background_tasks import BackgroundTask, BackgroundTasks
    from litestar.datastructures import Cookie
    from litestar.response.base import ASGIResponse
    from litestar.types import ASGIApp, HTTPScope, Message, Receive, Scope, Send, TypeEncodersMap

JSON_SUFFIX = '.json'
API_PREFIX = '/api/'
# Pages under /api/ that people see, as r2 had them: where an app sends someone to let it in.
API_PAGES = frozenset({'/api/v1/authorize'})
WANTS_JSON = 'wants_json'
EDGE_CACHE = 'public, max-age=60'
# Scripts only from our own files: no inline script, no eval.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data: https:; script-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
NO_CACHE = 'private, no-store'
FORM_TYPES = frozenset({RequestEncodingType.URL_ENCODED, RequestEncodingType.MULTI_PART})


def strip_json_suffix(scope: HTTPScope) -> None:
    """Route ``/in/pics/hot.json`` as ``/in/pics/hot``, remembering that JSON was asked for.

    This runs before routing, so every page route answers with a ``.json`` suffix without
    registering a second path. ``/.json`` is the front page.
    """
    path = scope['path']
    if not path.endswith(JSON_SUFFIX):
        return
    stripped = path.removesuffix(JSON_SUFFIX) or '/'
    scope['path'] = stripped
    scope['raw_path'] = stripped.encode()
    scope.setdefault('state', {})[WANTS_JSON] = True


async def api_type_guard(
    connection: ASGIConnection[Any, Any, Any, Any], _: BaseRouteHandler
) -> None:
    """Honor ``api_type=json`` sent in a form body, as the site's JS sends it.

    Not an authorization check: it runs as the first guard so that it reads the (cached) form
    before anything else can fail, and every error after it can answer in JSON.
    """
    if not isinstance(connection, Request) or connection.method != 'POST':
        return
    if connection.content_type[0] not in FORM_TYPES:
        return
    if (await connection.form()).get('api_type') == 'json':
        connection.scope.setdefault('state', {})[WANTS_JSON] = True


# Headers for whatever response a request gets, error or not; the bearer guard puts its rate
# limit here.
EXTRA_HEADERS = 'extra_headers'


def extra_headers_middleware(app: ASGIApp) -> ASGIApp:
    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != ScopeType.HTTP:
            await app(scope, receive, send)
            return

        async def sending(message: Message) -> None:
            extra = scope.get('state', {}).get(EXTRA_HEADERS)
            if message['type'] == 'http.response.start' and extra:
                headers = list(message.get('headers', []))
                headers += [
                    (name.lower().encode(), value.encode()) for name, value in extra.items()
                ]
                message = {**message, 'headers': headers}
            await send(message)

        await app(scope, receive, sending)

    return handle


def wants_json(request: Request[Any, Any, Any]) -> bool:
    if request.scope.get('state', {}).get(WANTS_JSON):
        return True
    # GETs under /api/ are API calls; only form posts there fall back to HTML.
    path = request.url.path
    if request.method == 'GET' and path.startswith(API_PREFIX) and path not in API_PAGES:
        return True
    if request.query_params.get('api_type') == 'json':
        return True
    best = request.accept.best_match([MediaType.HTML, MediaType.JSON], default=MediaType.HTML)
    return best == MediaType.JSON


class Rendered(Response[Any]):
    """The app's response class: a handler returns a ``View`` and this decides its output."""

    @override
    def to_asgi_response(
        self,
        app: Litestar | None,
        request: Request[Any, Any, Any],
        *,
        background: BackgroundTask | BackgroundTasks | None = None,
        cookies: Iterable[Cookie] | None = None,
        encoded_headers: Iterable[tuple[bytes, bytes]] | None = None,
        headers: dict[str, str] | None = None,
        is_head_response: bool = False,
        media_type: MediaType | str | None = None,
        status_code: int | None = None,
        type_encoders: TypeEncodersMap | None = None,
    ) -> ASGIResponse:
        view = self.content
        if isinstance(view, View):
            self._render(request, view)
        return super().to_asgi_response(
            app,
            request,
            background=background,
            cookies=cookies,
            encoded_headers=encoded_headers,
            headers=headers,
            is_head_response=is_head_response,
            media_type=media_type,
            status_code=status_code,
            type_encoders=type_encoders,
        )

    def _render(self, request: Request[Any, Any, Any], view: View) -> None:
        self.status_code = view.status_code
        if isinstance(view, FormResult) and not wants_json(request):
            # A page without a redirect is the answer itself, such as one showing a new secret.
            if view.page is not None and (view.errors or view.redirect is None):
                self._render_html(request, view.page)
            else:
                self.status_code = HTTP_303_SEE_OTHER
                self.headers['Location'] = view.redirect or '/'
                self.content = b''
        elif view.json_only or (wants_json(request) and view.has_json):
            self.content = view.to_json()
            self.media_type = MediaType.JSON
        elif wants_json(request):
            self.content = {'json': {'errors': [FieldError(ErrorCode.NOT_FOUND).to_json()]}}
            self.media_type = MediaType.JSON
            self.status_code = HTTP_404_NOT_FOUND
        else:
            self._render_html(request, view)
        cacheable = (
            view.cacheable
            and request.method == 'GET'
            and self.status_code == HTTP_200_OK
            and sessions.COOKIE not in request.cookies
            and 'authorization' not in request.headers
        )
        self.headers.setdefault('Cache-Control', EDGE_CACHE if cacheable else NO_CACHE)
        # The edge caches by path and query; the session cookie, an app's token and Accept
        # change the body.
        self.headers.setdefault('Vary', 'Accept, Authorization, Cookie')

    def _render_html(self, request: Request[Any, Any, Any], view: View) -> None:
        self.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
        engine = request.app.template_engine
        if engine is None:
            msg = "rendering a view needs the app's template_config"
            raise RuntimeError(msg)
        with timed('render', view.template):
            self.content = engine.get_template(view.template).render(view=view, request=request)
        self.media_type = MediaType.HTML
