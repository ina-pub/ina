from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.errors import ErrorCode, InaError
from app.repos.base import Community, CommunitySettings, ModPermission
from app.services import perms
from app.services.communities import CommunityForm, _Errors, can_create, name_errors, text_from_form
from app.services.perms import Action, Standing
from app.settings import Settings
from tests.services.test_sessions import ACCOUNT


@pytest.mark.parametrize('name', ['pics', 'AskIna', 'a_b', 'x' * 21, '9gag'])
def test_good_names(name: str) -> None:
    assert name_errors(name) == []


@pytest.mark.parametrize('name', ['ab', 'x' * 22, '_pics', 'pi cs', 'pics!', 'All', 'random'])
def test_bad_names(name: str) -> None:
    assert [e.code for e in name_errors(name)] == [ErrorCode.BAD_COMMUNITY_NAME]


def test_text_renders_markdown_and_keeps_other_settings() -> None:
    current = CommunitySettings(sticky_fullnames=('t3_1',), wiki_edit_karma=5)
    form = CommunityForm(
        title=' Pictures ',
        public_description='only *pics*',
        description='>!rules!<',
        submit_text='no memes',
        over_18='on',
        allow_top='',
        wiki_edit_karma='50',
    )
    errors = _Errors()
    text = text_from_form(form, current, errors)
    assert errors.found == []
    assert text.title == 'Pictures'
    assert text.description_html == '<p>only <em>pics</em></p>\n'
    assert text.sidebar_html == '<p><span class="md-spoiler-text">rules</span></p>\n'
    assert text.settings.submit_text_html == '<p>no memes</p>\n'
    assert text.nsfw is True
    assert text.settings.allow_top is False
    assert text.settings.wiki_edit_karma == 50
    assert text.settings.sticky_fullnames == ('t3_1',)


def test_text_collects_every_error() -> None:
    form = CommunityForm(
        title='',
        public_description='x' * 501,
        type='secret',
        spam_links='some',
        comment_score_hide_mins='1441',
        wiki_edit_age='-1',
        suggested_comment_sort='random',
    )
    errors = _Errors()
    text_from_form(form, CommunitySettings(), errors)
    assert [(e.code, e.field) for e in errors.found] == [
        (ErrorCode.NO_TEXT, 'title'),
        (ErrorCode.TOO_LONG, 'public_description'),
        (ErrorCode.INVALID_OPTION, 'suggested_comment_sort'),
        (ErrorCode.INVALID_OPTION, 'spam_links'),
        (ErrorCode.BAD_NUMBER, 'comment_score_hide_mins'),
        (ErrorCode.BAD_NUMBER, 'wiki_edit_age'),
        (ErrorCode.INVALID_OPTION, 'type'),
    ]
    with pytest.raises(InaError):
        errors.raise_any()


def test_can_create_needs_age_or_karma() -> None:
    settings = Settings(
        create_community_account_age_days=30,
        create_community_link_karma=10,
        create_community_comment_karma=10,
    )
    now = ACCOUNT.created + timedelta(days=31)
    assert not can_create(settings, ACCOUNT, now=ACCOUNT.created + timedelta(days=1))
    assert not can_create(settings, replace(ACCOUNT, link_karma=0, comment_karma=0), now=now)
    assert can_create(settings, replace(ACCOUNT, link_karma=10), now=now)
    assert can_create(Settings(), ACCOUNT, now=datetime(2026, 11, 1, tzinfo=UTC))


PUBLIC = Community(
    id=1,
    name='pics',
    title='Pictures',
    description_md='',
    description_html='',
    sidebar_md='',
    sidebar_html='',
    type='public',
    nsfw=False,
    settings=CommunitySettings(),
    subscriber_count=0,
    created=datetime(2026, 11, 1, tzinfo=UTC),
)
PRIVATE = replace(PUBLIC, type='private')


def test_permissions() -> None:
    assert perms.can(None, Action.VIEW, PUBLIC)
    assert not perms.can(None, Action.VIEW, PRIVATE)
    assert perms.can(ACCOUNT, Action.VIEW, PRIVATE, standing=Standing(ModPermission.POSTS))
    assert not perms.can(None, Action.SUBSCRIBE, PUBLIC)
    assert perms.can(ACCOUNT, Action.SUBSCRIBE, PUBLIC)
    assert not perms.can(ACCOUNT, Action.SUBSCRIBE, PRIVATE)
    assert not perms.can(ACCOUNT, Action.CONFIGURE, PUBLIC)
    assert not perms.can(ACCOUNT, Action.CONFIGURE, PUBLIC, standing=Standing(ModPermission.POSTS))
    assert perms.can(ACCOUNT, Action.CONFIGURE, PUBLIC, standing=Standing(ModPermission.ALL))
