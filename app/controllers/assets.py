"""The site stylesheet bundle, under a name that changes with its content."""

from __future__ import annotations

from typing import Any

from litestar import Request, Response, get
from litestar.exceptions import NotFoundException
from litestar.params import FromPath

# The name changes whenever the content does, so it never needs revalidating.
IMMUTABLE = 'public, max-age=31536000, immutable'


@get('/assets/{name:str}')
async def show_asset(request: Request[Any, Any, Any], name: FromPath[str]) -> Response[bytes]:
    bundle = request.app.state.assets.css
    if name != bundle.name:
        raise NotFoundException
    return Response(
        content=bundle.body,
        media_type='text/css; charset=utf-8',
        headers={'Cache-Control': IMMUTABLE},
    )
