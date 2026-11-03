from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.errors import InaError
from app.repos.base import Community, CommunitySettings, ModPermission
from app.services.perms import Standing
from app.services.wiki import WikiAccess, may_revise, may_view, normalize
from tests.services.test_bouncer import author

NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ('raw', 'name'),
    [
        ('', 'index'),
        ('Some Page', 'some_page'),
        ('/a/b/../c', 'a/c'),
        ('config/sidebar', 'config/sidebar'),
    ],
)
def test_names_normalize_as_on_r2(raw: str, name: str) -> None:
    assert normalize(raw) == name


@pytest.mark.parametrize('raw', ['..', 'a.b', 'edit/x', 'revisions/x', 'pages', 'naïve'])
def test_impossible_names(raw: str) -> None:
    with pytest.raises(InaError):
        normalize(raw)


def community(**settings: Any) -> Community:  # noqa: ANN401
    return Community(
        id=1,
        name='pics',
        title='Pictures',
        description_md='',
        description_html='',
        sidebar_md='',
        sidebar_html='',
        type='public',
        nsfw=False,
        settings=CommunitySettings(**{'wikimode': 'anyone', **settings}),
        subscriber_count=0,
        created=NOW,
    )


def test_may_revise_follows_r2() -> None:
    home = community(wiki_edit_karma=10, wiki_edit_age=5)
    old = author(age=timedelta(days=30), karma=50)
    new = author(age=timedelta(days=1), karma=50)
    poor = author(age=timedelta(days=30), karma=3)
    reader = WikiAccess(old, Standing())
    assert may_revise(reader, home, 'faq', 0, editor=False, now=NOW)
    assert not may_revise(WikiAccess(new, Standing()), home, 'faq', 0, editor=False, now=NOW)
    assert not may_revise(WikiAccess(poor, Standing()), home, 'faq', 0, editor=False, now=NOW)
    # A page's own editors pass every minimum; level 1 lets only them in.
    assert may_revise(WikiAccess(poor, Standing()), home, 'faq', 1, editor=True, now=NOW)
    assert not may_revise(reader, home, 'faq', 1, editor=False, now=NOW)
    assert not may_revise(WikiAccess(old, Standing(), banned=True), home, 'faq', 0, editor=False)
    assert not may_revise(WikiAccess(None, Standing()), home, 'faq', 0, editor=False)

    wiki_mod = WikiAccess(old, Standing(mod=ModPermission.WIKI))
    config_mod = WikiAccess(old, Standing(mod=ModPermission.WIKI | ModPermission.CONFIG))
    assert may_revise(wiki_mod, home, 'faq', 2, editor=False)
    # Config pages need the 'config' permission as well.
    assert not may_revise(wiki_mod, home, 'config/sidebar', 0, editor=False)
    assert may_revise(config_mod, home, 'config/sidebar', 0, editor=False)
    assert not may_view(reader, 'config/bouncer', 0)
    assert may_view(reader, 'config/sidebar', 0)
    assert not may_view(reader, 'faq', 2)
