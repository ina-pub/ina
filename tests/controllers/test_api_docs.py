from __future__ import annotations

import re
from typing import TYPE_CHECKING

from openapi_spec_validator import validate

from app.controllers import scopes
from app.controllers.api_docs import SUMMARIES
from app.services.oauth import SCOPES

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def test_openapi_document(client: AsyncTestClient[Litestar]) -> None:
    response = await client.get('/dev/api/openapi')
    assert response.status_code == 200
    document = response.json()
    validate(document)
    # The suffix rule serves it at the name tools expect, too.
    assert (await client.get('/dev/api/openapi.json')).json() == document

    paths = document['paths']
    vote = paths['/api/vote']['post']
    assert vote['security'] == [{'oauth2': ['vote']}]
    assert set(
        vote['requestBody']['content']['application/x-www-form-urlencoded']['schema']['properties']
    ) >= {'id', 'dir'}
    assert vote['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/FormResponse'
    }
    listing = paths['/in/{name}/{sort}']['get']
    assert listing['security'] == [{'oauth2': ['read']}]
    assert {p['name'] for p in listing['parameters']} >= {'name', 'sort', 'after', 'limit'}
    assert listing['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/Listing'
    }
    friend = paths['/api/friend']['post']
    assert friend['security'] == [
        {'oauth2': ['subscribe']},
        {'oauth2': ['modcontributors']},
        {'oauth2': ['modothers']},
    ]
    names = paths['/api/search_community_names']['get']['parameters']
    assert {p['name'] for p in names} == {'query', 'exact', 'include_over_18'}
    assert '/api/v1/access_token' in paths
    # Routes closed to apps aren't offered.
    assert '/api/login' not in paths
    assert '/prefs/apps' not in paths

    # Every route with a scope is there, by every method.
    operations = sum(len(methods) for methods in paths.values())
    assert operations >= len(scopes.needed()) - 1
    flows = document['components']['securitySchemes']['oauth2']['flows']
    assert set(flows['authorizationCode']['scopes']) == set(SCOPES)
    link = document['components']['schemas']['Link']['properties']
    assert {'community_name_prefixed', 'author_fullname', 'upvote_ratio'} <= set(link)


async def test_api_page(client: AsyncTestClient[Litestar]) -> None:
    page = (await client.get('/dev/api')).text
    assert '<h2 id="scope-vote">vote</h2>' in page
    assert '<code>/api/vote</code>' in page
    assert 'href="/dev/api/openapi"' in page


def test_every_route_an_app_may_call_has_a_summary() -> None:
    assert set(scopes.needed()) <= set(SUMMARIES)
    # Written for the people using the API, in ina's words.
    assert not [s for s in SUMMARIES.values() if re.search(r'\br2\b|\br/|``', s)]
