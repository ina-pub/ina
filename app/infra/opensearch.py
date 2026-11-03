"""An OpenSearch client over its REST API. Nothing in OpenSearch is authoritative.

Indices are named ``{prefix}-{name}`` and created, with the mappings given, on first use.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import msgspec

from app.infra.timing import timed

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

TIMEOUT = httpx.Timeout(5.0)
NOT_FOUND = 404
BAD_REQUEST = 400

type Body = dict[str, Any]


class SearchUnavailableError(Exception):
    """OpenSearch could not be reached or refused the request."""


@dataclass(frozen=True, slots=True)
class Hits:
    ids: list[str]
    total: int


class SearchClient:
    def __init__(
        self,
        url: str,
        *,
        prefix: str,
        indices: Mapping[str, Body],
        refresh: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = httpx.AsyncClient(base_url=url, timeout=TIMEOUT, transport=transport)
        self.prefix = prefix
        self.indices = indices
        self.refresh = 'true' if refresh else 'false'
        self._ready = False
        self._lock = asyncio.Lock()

    def index(self, name: str) -> str:
        return f'{self.prefix}-{name}'

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Body | None = None,
        content: bytes | None = None,
        params: dict[str, str] | None = None,
        allow: Iterable[int] = (),
    ) -> httpx.Response:
        headers = {'Content-Type': 'application/x-ndjson' if content else 'application/json'}
        if content is None and body is not None:
            content = msgspec.json.encode(body)
        try:
            with timed('search', f'{method} {path}'):
                response = await self.http.request(
                    method, path, content=content, params=params, headers=headers
                )
        except httpx.HTTPError as error:
            msg = f'{method} {path}: {error!r}'
            raise SearchUnavailableError(msg) from error
        if response.is_error and response.status_code not in set(allow):
            msg = f'{method} {path}: {response.status_code} {response.text[:300]}'
            raise SearchUnavailableError(msg)
        return response

    async def ensure_indices(self) -> None:
        """Create any missing index; a no-op after the first success in this process."""
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            for name, settings in self.indices.items():
                found = await self._request('HEAD', f'/{self.index(name)}', allow={NOT_FOUND})
                if found.status_code == NOT_FOUND:
                    # Another process may create it first: that 400 is fine.
                    await self._request(
                        'PUT', f'/{self.index(name)}', body=settings, allow={BAD_REQUEST}
                    )
            self._ready = True

    async def recreate(self) -> None:
        """Drop and recreate every index, empty, with the current mappings."""
        async with self._lock:
            for name in self.indices:
                await self._request('DELETE', f'/{self.index(name)}', allow={NOT_FOUND})
            self._ready = False
        await self.ensure_indices()

    async def put(self, name: str, doc_id: str, document: Body) -> None:
        await self.ensure_indices()
        await self._request(
            'PUT',
            f'/{self.index(name)}/_doc/{doc_id}',
            body=document,
            params={'refresh': self.refresh},
        )

    async def delete(self, name: str, doc_id: str) -> None:
        await self.ensure_indices()
        await self._request(
            'DELETE',
            f'/{self.index(name)}/_doc/{doc_id}',
            params={'refresh': self.refresh},
            allow={NOT_FOUND},
        )

    async def bulk(
        self, name: str, documents: Iterable[tuple[str, Body]], deletions: Iterable[str] = ()
    ) -> int:
        """Write and delete many documents at once; return how many actions were sent."""
        await self.ensure_indices()
        lines = []
        for doc_id, document in documents:
            lines.append(msgspec.json.encode({'index': {'_id': doc_id}}))
            lines.append(msgspec.json.encode(document))
        lines.extend(msgspec.json.encode({'delete': {'_id': doc_id}}) for doc_id in deletions)
        if not lines:
            return 0
        response = await self._request(
            'POST',
            f'/{self.index(name)}/_bulk',
            content=b'\n'.join(lines) + b'\n',
            params={'refresh': self.refresh},
        )
        result = response.json()
        # Deleting a document that is not there is fine.
        failed = [
            outcome
            for item in result['items']
            for action, outcome in item.items()
            if 'error' in outcome and not (action == 'delete' and outcome['status'] == NOT_FOUND)
        ]
        if failed:
            msg = f'bulk into {name}: {len(failed)} failed, first: {failed[0]}'
            raise SearchUnavailableError(msg)
        return len(result['items'])

    async def search(self, name: str, body: Body) -> Hits:
        await self.ensure_indices()
        response = await self._request('POST', f'/{self.index(name)}/_search', body=body)
        hits = response.json()['hits']
        return Hits(ids=[hit['_id'] for hit in hits['hits']], total=hits['total']['value'])

    async def aclose(self) -> None:
        await self.http.aclose()
