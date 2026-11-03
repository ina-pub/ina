from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_comments import reply
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.controllers.test_moderation import act, modlog
from tests.helpers import codes, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def setup(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> tuple[str, str, str]:
    """Alice moderates /in/pics; bob has posted t3_1 there."""
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    link = await submitted(other_client, bob, title='my dog', url='https://dog.example/')
    return alice, bob, link


async def api(client: AsyncTestClient[Litestar], path: str, modhash: str, **form: str) -> Any:  # noqa: ANN401
    return await act(client, f'/in/pics/api/{path}', modhash, **form)


async def config(client: AsyncTestClient[Litestar], modhash: str, **settings: str) -> None:
    form = {'flair_enabled': 'on', 'flair_position': 'right', 'link_flair_position': 'right'}
    response = await api(client, 'flairconfig', modhash, **{**form, **settings})
    assert response.json()['json']['errors'] == []


async def author_flair(client: AsyncTestClient[Litestar]) -> tuple[Any, Any]:
    link = (await client.get('/in/pics/new.json')).json()['data']['children'][0]['data']
    return link['author_flair_text'], link['author_flair_css_class']


async def test_moderators_set_flair(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob, _ = await setup(client, other_client)
    set_flair = await api(client, 'flair', alice, name='bob', text='Good dog', css_class='gold big')
    assert set_flair.json()['json']['errors'] == []
    assert await author_flair(other_client) == ('Good dog', 'gold big')
    page = (await other_client.get('/in/pics/')).text
    assert '<span class="flair flair-gold flair-big" title="Good dog">Good dog</span>' in page
    listed = (await client.get('/in/pics/api/flairlist')).json()
    assert listed == {
        'users': [{'user': 'bob', 'flair_text': 'Good dog', 'flair_css_class': 'gold big'}]
    }

    refused = await api(other_client, 'flair', bob, name='bob', text='Best dog')
    assert refused.status_code == 403
    bad = await api(client, 'flair', alice, name='bob', css_class='no.dots')
    assert codes(bad.json()['json']) == [('BAD_CSS_NAME', 'css_class')]
    long = await api(client, 'flair', alice, name='bob', text='x' * 65)
    assert codes(long.json()['json']) == [('TOO_LONG', 'text')]

    await api(client, 'deleteflair', alice, name='bob')
    assert await author_flair(other_client) == (None, None)
    log = (await client.get('/in/pics/about/log.json')).json()['data']['children']
    assert [(a['data']['action'], a['data']['details']) for a in log] == [
        ('editflair', 'flair_delete'),
        ('editflair', 'flair_edit'),
    ]


async def test_flair_shows_only_where_switched_on(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob, link = await setup(client, other_client)
    await api(client, 'flair', alice, name='bob', text='Good dog')
    await reply(other_client, bob, link, 'woof')
    thread = (await client.get('/in/pics/comments/1/.json')).json()
    assert thread[1]['data']['children'][0]['data']['author_flair_text'] == 'Good dog'
    # Bob may hide his own flair here.
    await api(other_client, 'setflairenabled', bob, flair_enabled='')
    assert await author_flair(client) == (None, None)
    await api(other_client, 'setflairenabled', bob, flair_enabled='on')
    assert await author_flair(client) == ('Good dog', None)
    # And the community may turn flair off altogether.
    await config(client, alice, flair_enabled='')
    assert await author_flair(client) == (None, None)
    assert await modlog(client) == ['editflair', 'editflair', 'editflair']


async def test_users_pick_their_own_where_allowed(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob, _ = await setup(client, other_client)
    fixed = await api(client, 'flairtemplate', alice, text='Dog person', css_class='dog')
    editable = await api(
        client, 'flairtemplate', alice, text='Pet', css_class='pet', text_editable='on'
    )
    fixed_id = fixed.json()['json']['data']['flair_template_id']
    editable_id = editable.json()['json']['data']['flair_template_id']
    refused = await api(other_client, 'selectflair', bob, flair_template_id=fixed_id)
    assert refused.status_code == 403

    await config(client, alice, flair_self_assign_enabled='on')
    choices = (await api(other_client, 'flairselector', bob)).json()
    assert [c['flair_text'] for c in choices['choices']] == ['Dog person', 'Pet']
    # Text bob may not edit stays the template's.
    await api(other_client, 'selectflair', bob, flair_template_id=fixed_id, text='Cat person')
    assert await author_flair(client) == ('Dog person', 'dog')
    await api(other_client, 'selectflair', bob, flair_template_id=editable_id, text='Hamster')
    assert await author_flair(client) == ('Hamster', 'pet')
    sidebar = (await other_client.get('/in/pics/')).text
    assert 'show it beside my name' in sidebar
    assert f'value="{editable_id}"' in sidebar
    await api(other_client, 'selectflair', bob, flair_template_id='')
    assert await author_flair(client) == (None, None)
    # Bob may not pick flair for alice.
    other = await api(other_client, 'selectflair', bob, name='alice', flair_template_id=fixed_id)
    assert other.status_code == 403

    await api(client, 'deleteflairtemplate', alice, flair_template_id=fixed_id)
    await api(client, 'clearflairtemplates', alice, flair_type='LINK_FLAIR')
    page = (await client.get('/in/pics/about/flair.json')).json()
    assert [t['flair_text'] for t in page['user_templates']] == ['Pet']


async def test_link_flair(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob, link = await setup(client, other_client)
    await config(client, alice)
    await api(client, 'flair', alice, link=link, text='Cute', css_class='cute')
    data = (await other_client.get('/in/pics/new.json')).json()['data']['children'][0]['data']
    assert (data['link_flair_text'], data['link_flair_css_class']) == ('Cute', 'cute')
    page = (await other_client.get('/in/pics/')).text
    assert 'title="Cute">Cute</span>' in page
    assert ' linkflair linkflair-cute"' in page

    template = await api(
        client, 'flairtemplate', alice, flair_type='LINK_FLAIR', text='Question', css_class='q'
    )
    template_id = template.json()['json']['data']['flair_template_id']
    refused = await api(other_client, 'selectflair', bob, link=link, flair_template_id=template_id)
    assert refused.status_code == 403
    await config(client, alice, link_flair_self_assign_enabled='on')
    assert 'set flair' in (await other_client.get('/in/pics/comments/1/')).text
    await api(other_client, 'selectflair', bob, link=link, flair_template_id=template_id)
    data = (await client.get('/in/pics/new.json')).json()['data']['children'][0]['data']
    assert data['link_flair_text'] == 'Question'
    # Submitters flair only their own links.
    alices = await submitted(client, alice, title='mine', url='https://a.example/')
    other = await api(other_client, 'selectflair', bob, link=alices, flair_template_id=template_id)
    assert other.status_code == 403


async def test_flair_csv(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _, _ = await setup(client, other_client)
    rows = 'bob,Good dog,gold\nnobody,x,y\nbob,,bad.css\nalice,Mod,'
    results = (await api(client, 'flaircsv', alice, flair_csv=rows)).json()
    assert [(r['ok'], r['status'], r['errors']) for r in results] == [
        (True, 'added flair for user bob', {}),
        (False, '', {'user': 'skipped: no account is named nobody'}),
        (False, '', {'css': "invalid css class `bad.css', ignoring"}),
        (True, 'added flair for user alice', {}),
    ]
    users = (await client.get('/in/pics/api/flairlist')).json()['users']
    assert [(u['user'], u['flair_text']) for u in users] == [('alice', 'Mod'), ('bob', 'Good dog')]
