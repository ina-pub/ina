from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from app.controllers.render import WANTS_JSON, strip_json_suffix

if TYPE_CHECKING:
    from litestar.types import HTTPScope


def scope_for(path: str) -> HTTPScope:
    return cast('HTTPScope', {'type': 'http', 'path': path, 'raw_path': path.encode()})


@pytest.mark.parametrize(
    ('path', 'routed'),
    [('/in/pics/hot.json', '/in/pics/hot'), ('/.json', '/'), ('/health.json', '/health')],
)
def test_json_suffix_is_stripped(path: str, routed: str) -> None:
    scope = scope_for(path)
    strip_json_suffix(scope)
    assert scope['path'] == routed
    assert scope['raw_path'] == routed.encode()
    assert scope['state'][WANTS_JSON] is True


def test_other_paths_are_untouched() -> None:
    scope = scope_for('/in/pics/hot')
    strip_json_suffix(scope)
    assert scope['path'] == '/in/pics/hot'
    assert 'state' not in scope
