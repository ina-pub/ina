from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.repos.base import Account, Comment, Link, Prefs
from app.services.bouncer import Item, RuleError, fill, matched, parse

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def link(**overrides: object) -> Link:
    values: dict[str, object] = {
        'id': 1,
        'community_id': 1,
        'community_name': 'pics',
        'author_id': 2,
        'author_name': 'bob',
        'title': 'My cat, photographed',
        'url': 'https://i.example.com/cat.jpg',
        'domain': 'i.example.com',
        'is_self': False,
        'selftext_md': '',
        'selftext_html': '',
        'nsfw': False,
        'spoiler': False,
        'flair_text': None,
        'flair_css': None,
        'score': 1,
        'upvotes': 1,
        'downvotes': 0,
        'hot_score': 0.0,
        'controversy_score': 0.0,
        'comment_count': 0,
        'created': NOW,
        'edited': None,
        'removed': False,
        'spam': False,
        'deleted': False,
        'locked': False,
        'stickied': False,
        'suggested_sort': None,
        'contest_mode': False,
        'sendreplies': True,
        'sticky_comment_id': None,
        'distinguished': None,
        'approved_by': None,
        'ignore_reports': False,
        'reported': 0,
    }
    return Link(**{**values, **overrides})  # type: ignore[arg-type]


def comment(body: str, *, parent_id: int | None = None) -> Comment:
    return Comment(
        id=5,
        link_id=1,
        parent_id=parent_id,
        path='5',
        depth=0 if parent_id is None else 1,
        author_id=2,
        author_name='bob',
        community_id=1,
        community_name='pics',
        body_md=body,
        body_html='',
        score=1,
        upvotes=1,
        downvotes=0,
        confidence=0.0,
        controversy_score=0.0,
        created=NOW,
        edited=None,
        removed=False,
        spam=False,
        deleted=False,
        distinguished=None,
        sendreplies=True,
        approved_by=None,
        ignore_reports=False,
        reported=0,
    )


def author(*, age: timedelta = timedelta(days=100), karma: int = 50) -> Account:
    return Account(
        id=2,
        name='bob',
        password_hash='',
        email=None,
        email_verified=False,
        created=NOW - age,
        link_karma=karma,
        comment_karma=karma,
        prefs=Prefs(),
        deleted=False,
        suspended=False,
        in_timeout=False,
        inbox_count=0,
        session_version=0,
    )


def item(
    thing: Link | None = None,
    reply: Comment | None = None,
    who: Account | None = None,
    *,
    moderator: bool = False,
) -> Item:
    return Item(
        link=thing or link(),
        comment=reply,
        author=who or author(),
        author_is_moderator=moderator,
        permalink='/in/pics/comments/1/my_cat/',
    )


def first(text: str, target: Item) -> str | None:
    return matched(parse(text)[0], target, NOW)


def test_words_match_whole_words_by_default() -> None:
    assert first('title: cat', item()) == 'cat'
    assert first('title: ca', item()) is None
    assert first('title (includes): ca', item()) == 'ca'
    # Case-insensitive by default; {{match}} is the text as written.
    assert first('title: [dog, CAT]', item()) == 'cat'
    assert first('title (case-sensitive): CAT', item()) is None


def test_modifiers_and_fields() -> None:
    assert first('title (starts-with): my', item()) == 'My'
    assert first('title (ends-with): photographed', item()) == 'photographed'
    assert first('title (full-exact): My cat', item()) is None
    assert first("title (regex): 'c[a-z]t'", item()) == 'cat'
    # url defaults to includes; id to full-exact.
    assert first('url: example.com/ca', item()) == 'example.com/ca'
    assert first('id: 1', item()) == '1'
    assert first('title+domain: i.example.com', item()) == 'i.example.com'
    # Negation passes when nothing matches.
    assert first('~title: dog', item()) == ''
    assert first('~title: cat', item()) is None


def test_types_and_checks() -> None:
    text = item(link(is_self=True, selftext_md='short'))
    assert first('type: link submission\ntitle: cat', text) is None
    assert first('type: text submission\nbody_shorter_than: 10', text) == ''
    assert first('type: comment\nbody: cat', item()) is None
    top, nested = comment('a cat'), comment('a cat', parent_id=3)
    assert first('type: comment\nis_top_level: true', item(reply=top)) == ''
    assert first('type: comment\nis_top_level: true', item(reply=nested)) is None
    assert first('type: comment\nbody: cat', item(reply=top)) == 'cat'
    assert first('reports: 1', item()) is None
    assert first('reports: 1', item(link(reported=2))) == ''


def test_author_checks() -> None:
    young = item(who=author(age=timedelta(days=2), karma=3))
    rule = 'author:\n  account_age: < 7 days\n  combined_karma: < 10'
    assert first(rule, young) == ''
    assert first(rule, item()) is None
    assert first('author:\n  account_age: "> 3"', young) is None
    assert first('author:\n  name: [alice, bob]', item()) == ''
    assert first('author:\n  ~name: bob', item()) is None


def test_moderators_are_exempt_from_removals_by_default() -> None:
    assert first('title: cat\naction: remove', item(moderator=True)) is None
    assert first('title: cat\naction: approve', item(moderator=True)) == 'cat'
    rule = 'title: cat\naction: remove\nmoderators_exempt: false'
    assert first(rule, item(moderator=True)) == 'cat'


def test_rules_sort_by_priority() -> None:
    rules = parse('title: a\n---\npriority: 5\ntitle: b\n---\npriority: -1\ntitle: c\n')
    assert [rule.matches[0].values for rule in rules] == [('b',), ('a',), ('c',)]
    assert parse('') == []


@pytest.mark.parametrize(
    ('text', 'error'),
    [
        ('title: [', 'not valid YAML'),
        ('- a list', 'rule 1: expected a set of keys and values'),
        ('title: a\n---\ncolour: red', 'rule 2: "colour" is not a field'),
        ('type: anything', 'unknown type'),
        ('title (sometimes): a', 'unknown modifier "sometimes"'),
        ('title (regex): "("', 'bad regex'),
        ('action: delete', 'unknown action'),
        ('reports: lots', 'reports: expected a number'),
        ('type: comment\ntitle: a', '"title" is not a field'),
        ('author:\n  account_age: < a while', 'cannot read the period'),
    ],
)
def test_bad_rules_say_what_is_wrong(text: str, error: str) -> None:
    with pytest.raises(RuleError, match=error):
        parse(text)


def test_placeholders() -> None:
    text = 'Hi {{author}}, "{{match}}" in {{ title }} on in/{{community}}: {{permalink}}'
    assert fill(text, item(), 'pics', 'cat') == (
        'Hi bob, "cat" in My cat, photographed on in/pics: /in/pics/comments/1/my_cat/'
    )
