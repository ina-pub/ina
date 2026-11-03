"""Make an account a site admin, or stop it being one; or list the admins.

    uv run python -m scripts.admins grant NAME
    uv run python -m scripts.admins revoke NAME
    uv run python -m scripts.admins list

The site never grants admin: whoever runs the site does, here. An admin's powers act only while
they have admin mode on, which asks for their password again.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.accounts import PgAccountRepo
from app.settings import get_settings


async def run(command: str, name: str | None = None) -> int:
    """Do ``command``; return the exit status."""
    engine = db.create_engine(get_settings())
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            accounts = PgAccountRepo(session)
            if command == 'list':
                for admin in await accounts.admins():
                    sys.stdout.write(f'{admin}\n')
                return 0
            account = await accounts.by_name(name or '')
            if account is None or account.deleted:
                sys.stderr.write(f'there is no account named {name!r}\n')
                return 1
            await accounts.set_admin(account.id, admin=command == 'grant')
            done = 'is now an admin' if command == 'grant' else 'is no longer an admin'
            sys.stdout.write(f'{account.name} {done}\n')
            return 0
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('grant', 'revoke'):
        commands.add_parser(command).add_argument('name')
    commands.add_parser('list')
    arguments = parser.parse_args()
    sys.exit(asyncio.run(run(arguments.command, getattr(arguments, 'name', None))))


if __name__ == '__main__':
    main()
