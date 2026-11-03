"""The API's JSON keeps r2's shapes, with ina's names.

Listing envelopes hold things, each a kind (t1 to t5, or more) and its data, with fullnames for
ids. These pin each kind's fields, so that changing one is a decision, not an accident.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.infra import db
from app.views.shapes import ACCOUNT, COMMENT, COMMUNITY, LINK, MESSAGE, PROFILE_COMMENT
from tests.controllers.test_comments import reply, thread_with_link
from tests.controllers.test_messages import compose
from tests.helpers import post_json, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings

LISTING = {'after', 'before', 'children', 'dist', 'modhash'}


def things(listing: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    assert listing['kind'] == 'Listing'
    assert set(listing['data']) == LISTING
    return [child['data'] for child in listing['data']['children'] if child['kind'] == kind]


async def test_shapes(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await thread_with_link(client)
    await reply(client, alice, 't3_1', 'hello')
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    await compose(other_client, bob, to='alice', subject='hi', text='there')

    listing = (await client.get('/in/pics/.json')).json()
    thread = (await client.get('/in/pics/comments/1/.json')).json()
    about = (await client.get('/in/pics/about.json')).json()
    profile = (await client.get('/user/alice/about.json')).json()
    me = (await client.get('/api/v1/me')).json()
    inbox = (await client.get('/message/inbox.json')).json()
    ins = (await client.get('/ins.json')).json()
    comments = (await client.get('/user/alice/comments.json')).json()
    form = await post_json(client, '/api/vote', {'uh': alice, 'id': 't3_9', 'dir': '1'})

    link = things(listing, 't3')[0]
    assert set(link) == LINK
    assert link['name'] == 't3_1'
    assert link['community'] == 'pics'
    assert link['community_id'] == 't5_1'
    assert link['community_name_prefixed'] == 'in/pics'
    assert link['author_fullname'] == 't2_1'
    assert link['upvote_ratio'] == 1.0
    # A link's page is a pair of listings: the link, then its comments.
    assert things(thread[0], 't3') == [link]

    comment = things(thread[1], 't1')[0]
    assert set(comment) == COMMENT
    assert comment['permalink'] == '/in/pics/comments/1/discuss/1/'
    assert comment['is_submitter'] is True
    assert set(things(comments, 't1')[0]) == PROFILE_COMMENT

    assert about['kind'] == 't5'
    assert set(about['data']) == COMMUNITY
    assert about['data']['display_name_prefixed'] == 'in/pics'
    assert set(things(ins, 't5')[0]) == COMMUNITY

    assert profile['kind'] == 't2'
    assert set(me) == ACCOUNT
    assert set(profile['data']) == ACCOUNT

    message = things(inbox, 't4')[0]
    assert set(message) == MESSAGE
    assert message['name'] == 't4_1'

    # A form's answer is r2's json envelope, with each error a [code, message, field] triple.
    assert form == {'errors': [['NO_THING_ID', "that post, comment or message isn't there", 'id']]}


async def test_ids_are_base36(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    """Below 36, base36 and decimal agree; past it, every id in the API must be base36."""
    engine = db.create_engine(settings)
    async with engine.begin() as connection:
        await connection.execute(text('alter table accounts alter column id restart with 100'))
        await connection.execute(text('alter table links alter column id restart with 1000'))
    await engine.dispose()
    alice = await thread_with_link(client)
    await register(other_client, 'bob')

    link = things((await client.get('/in/pics/.json')).json(), 't3')[0]
    assert (link['id'], link['name'], link['author_fullname']) == ('rs', 't3_rs', 't2_2s')
    assert link['permalink'].startswith('/in/pics/comments/rs/')
    assert (await client.get(f'{link["permalink"]}.json')).status_code == 200

    await post_json(client, '/api/friend', {'uh': alice, 'type': 'friend', 'name': 'bob'})
    await post_json(
        client, '/api/friend', {'uh': alice, 'community': 'pics', 'type': 'banned', 'name': 'bob'}
    )
    lists = ('/prefs/friends.json', '/in/pics/about/banned.json', '/in/pics/about/moderators.json')
    ids = [(await client.get(path)).json()['data']['children'][0]['id'] for path in lists]
    assert ids == ['t2_2t', 't2_2t', 't2_2s']
