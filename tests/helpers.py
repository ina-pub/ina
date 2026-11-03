"""Small helpers for driving the site the way a browser or the site's JS would."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.accounts import PgAccountRepo
from app.services import sessions

if TYPE_CHECKING:
    import httpx
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.jsontypes import JSON
    from app.repos.base import Account
    from app.settings import Settings

PASSWORD = 'hunter22'


async def post_json(
    client: AsyncTestClient[Litestar], path: str, form: dict[str, str]
) -> dict[str, Any]:
    """Post a form the way the site's JS does and return the ``json`` envelope."""
    response = await client.post(path, data={**form, 'api_type': 'json'})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()['json']
    return body


async def register(
    client: AsyncTestClient[Litestar], name: str = 'alice', email: str = ''
) -> dict[str, Any]:
    body = await post_json(
        client,
        '/api/register',
        {'user': name, 'passwd': PASSWORD, 'passwd2': PASSWORD, 'email': email},
    )
    assert body['errors'] == []
    return body


async def me(client: AsyncTestClient[Litestar]) -> httpx.Response:
    return await client.get('/api/v1/me')


def codes(body: dict[str, Any]) -> list[tuple[JSON, JSON]]:
    """The (code, field) pairs of a response's errors."""
    return [(code, field) for code, _, field in body['errors']]


def session_cookie(client: AsyncTestClient[Litestar]) -> str | None:
    return client.cookies.get(sessions.COOKIE)


async def find_account(settings: Settings, name: str) -> Account:
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session:
        account = await PgAccountRepo(session).by_name(name)
    await engine.dispose()
    assert account is not None
    return account


async def run_published(published: list[Message[Any]]) -> list[str]:
    """Run the captured job messages in order, as a worker would; return the actors run."""
    from app.jobs import bouncer, inbox, mail, media, scores, search, trees  # noqa: PLC0415

    actors = {
        'recount_link': scores.recount,
        'recount_comment': scores.recount_one_comment,
        'rebuild_tree': trees.rebuild,
        'deliver_comment': inbox.comment_delivery,
        'deliver_message': inbox.message_delivery,
        'send_password_reset': mail.password_reset,
        'bouncer_check': bouncer.check,
        'make_thumbnail': media.link_thumbnail,
        'index_link': search.link_indexing,
        'index_community': search.community_indexing,
    }
    ran = []
    while published:
        message = published.pop(0)
        await actors[message.actor_name](*message.args)
        ran.append(message.actor_name)
    return ran


class Pages(BaseHTTPRequestHandler):
    """Serves ``routes``: path to (status, content type, body), or a redirect's location."""

    routes: ClassVar[dict[str, tuple[int, str, bytes]]] = {}

    def do_GET(self) -> None:
        status, kind, body = self.routes.get(self.path, (404, 'text/plain', b'missing'))
        self.send_response(status)
        if status in {301, 302}:
            self.send_header('Location', body.decode())
            body = b''
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass
