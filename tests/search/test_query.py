from __future__ import annotations

import pytest

from app.search.documents import sites
from app.search.query import QueryError, clean, fielded, parse

CATS = {'multi_match': {'query': 'cats', 'fields': ['title^2', 'selftext'], 'operator': 'and'}}
DOGS = {'multi_match': {'query': 'dogs', 'fields': ['title^2', 'selftext'], 'operator': 'and'}}


def test_words_are_all_required() -> None:
    assert parse('cats') == CATS
    assert parse('cats dogs') == {'bool': {'must': [CATS, DOGS]}}
    assert parse('cats AND dogs') == parse('cats dogs')
    assert parse('   ') is None


def test_or_not_and_grouping() -> None:
    assert parse('cats OR dogs') == {'bool': {'should': [CATS, DOGS], 'minimum_should_match': 1}}
    assert parse('cats NOT dogs') == {'bool': {'must': [CATS, {'bool': {'must_not': [DOGS]}}]}}
    assert parse('cats -dogs') == parse('cats NOT dogs')
    # AND binds tighter than OR; parentheses override it.
    assert parse('a OR b c') == {
        'bool': {
            'should': [parse('a'), {'bool': {'must': [parse('b'), parse('c')]}}],
            'minimum_should_match': 1,
        }
    }
    assert parse('(a OR b) c') == {'bool': {'must': [parse('a OR b'), parse('c')]}}
    # Lower case is just a word.
    assert parse('cats or dogs') == {'bool': {'must': [CATS, parse('or'), DOGS]}}


def test_phrases() -> None:
    assert parse('"black cat"') == {
        'multi_match': {'query': 'black cat', 'fields': ['title^2', 'selftext'], 'type': 'phrase'}
    }


def test_fields() -> None:
    assert parse('in:Pics') == {'term': {'community': 'pics'}}
    assert parse('in:/in/pics') == {'term': {'community': 'pics'}}
    assert parse('author:u/Bob') == {'term': {'author': 'bob'}}
    assert parse('self:yes') == {'term': {'is_self': True}}
    assert parse('nsfw:0') == {'term': {'nsfw': False}}
    assert parse('site:www.Example.com') == {'term': {'sites': 'example.com'}}
    assert parse('url:example.com/a') == {'match_phrase': {'url': 'example.com/a'}}
    assert parse('title:cats') == {'match': {'title': {'query': 'cats', 'operator': 'and'}}}
    assert parse('title:"black cat"') == {'match_phrase': {'title': 'black cat'}}
    assert parse('flair: meta') == {'match': {'flair': {'query': 'meta', 'operator': 'and'}}}
    assert parse('-author:bob') == {'bool': {'must_not': [{'term': {'author': 'bob'}}]}}
    # Not a field: searched as a word.
    assert parse('re:zero') == {
        'multi_match': {'query': 're:zero', 'fields': ['title^2', 'selftext'], 'operator': 'and'}
    }


@pytest.mark.parametrize(
    ('text', 'error'),
    [
        ('"cats', 'a quote is not closed'),
        ('(cats', 'a parenthesis is not closed'),
        ('cats)', 'a parenthesis closes nothing'),
        ('cats AND', 'the query ends too soon'),
        ('OR cats', 'OR needs a term on each side'),
        ('self:maybe', 'self: expected yes or no'),
        ('title:', 'the query ends too soon'),
        ('title: )', 'title: nothing to search for'),
        ('""', 'an empty phrase'),
    ],
)
def test_unreadable_queries(text: str, error: str) -> None:
    with pytest.raises(QueryError, match=error):
        parse(text)


def test_clean_and_fielded() -> None:
    assert clean('"Cats (and) dogs!') == 'cats and dogs'
    assert clean('()') == ''
    assert fielded('author:bob')
    assert not fielded('cats')


def test_sites_include_parent_domains() -> None:
    assert sites('i.Example.com') == ['i.example.com', 'example.com']
    assert sites('www.example.com') == ['example.com']
    assert sites('localhost') == ['localhost']
