from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.infra.assets import Assets, build

if TYPE_CHECKING:
    from pathlib import Path

    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def test_pages_link_the_hashed_bundle(client: AsyncTestClient[Litestar]) -> None:
    page = await client.get('/')
    found = re.search(
        r'<link rel="stylesheet" href="(/assets/site\.[0-9a-f]{12}\.css)">', page.text
    )
    assert found is not None
    css = await client.get(found[1])
    assert css.status_code == 200
    assert css.headers['content-type'].startswith('text/css')
    assert css.headers['cache-control'] == 'public, max-age=31536000, immutable'
    # ina's stylesheet, and the images it uses from /static/img.
    assert b'--ina-accent:' in css.content
    assert b'url(/static/img/search.svg)' in css.content
    image = await client.get('/static/img/search.svg')
    assert image.status_code == 200
    # Any other name is gone: old bundles are not served stale.
    assert (await client.get('/assets/site.000000000000.css')).status_code == 404


def test_the_bundle_follows_its_files(tmp_path: Path) -> None:
    (tmp_path / '10-b.css').write_text('b { color: red }')
    (tmp_path / '00-a.css').write_text('a { color: blue }')
    assets = Assets(tmp_path)
    first = assets.css
    assert first.body == b'a{color:blue}b{color:red}'
    assert first == build(tmp_path)
    (tmp_path / '20-c.css').write_text('c { color: green }')
    assert assets.css.body == b'a{color:blue}b{color:red}c{color:green}'
    assert assets.css.name != first.name
