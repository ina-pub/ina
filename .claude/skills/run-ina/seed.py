"""Register `tester`, create in/pics, submit links, and run their find_embed jobs.

Usage (from the repo root, with env.sh sourced and the server up):
    PYTHONPATH=. uv run python .claude/skills/run-ina/seed.py 'Title=https://...' ...
Prints each link's fullname and what its `media` came out as.
"""

# Agent tooling, not a package: it reports by printing, and the password is a throwaway local one.
# ruff: noqa: INP001, S105, T201

from __future__ import annotations

import asyncio
import os
import sys

import httpx

from app.jobs.media import link_embed
from tests.controllers.test_communities import settings_form

PASSWORD = 'hunter2hunter2'


async def main(pairs: list[str]) -> None:
    base = os.environ.get('INA_RUN_URL', 'http://localhost:8765')
    names = []
    async with httpx.AsyncClient(base_url=base) as client:
        form = {'user': 'tester', 'passwd': PASSWORD, 'passwd2': PASSWORD, 'api_type': 'json'}
        body = (await client.post('/api/register', data=form)).json()['json']
        if body['errors']:
            # Already there from an earlier run: log in instead.
            form = {'user': 'tester', 'passwd': PASSWORD, 'api_type': 'json'}
            body = (await client.post('/api/login', data=form)).json()['json']
        uh = body['data']['modhash']
        await client.post('/api/site_admin', data={'uh': uh, 'api_type': 'json', **settings_form()})
        for pair in pairs:
            title, _, url = pair.partition('=')
            form = {'uh': uh, 'api_type': 'json', 'community': 'pics', 'kind': 'link'}
            body = await client.post('/api/submit', data={**form, 'title': title, 'url': url})
            result = body.json()['json']
            if result['errors']:
                print(title, 'not submitted:', result['errors'])
                continue
            names.append(result['data']['name'])
    for name in names:
        await link_embed(name)
    async with httpx.AsyncClient(base_url=base) as client:
        for child in (await client.get('/in/pics/new.json')).json()['data']['children']:
            data = child['data']
            media = data['media'] and data['media']['type']
            print(f'{data["name"]:6} {data["title"]:12} media={media}')


asyncio.run(main(sys.argv[1:]))
