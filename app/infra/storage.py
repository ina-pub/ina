"""An S3 client over httpx, signing with AWS Signature Version 4. Objects are put, got and deleted.

SeaweedFS serves S3 locally; anything that speaks S3 does in production.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

import httpx

from app.infra.timing import timed

if TYPE_CHECKING:
    from app.settings import Settings

TIMEOUT = httpx.Timeout(10.0)
NOT_FOUND = 404
CONFLICT = 409
EMPTY_HASH = hashlib.sha256(b'').hexdigest()


class StorageError(Exception):
    """The object store could not be reached or refused the request."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    body: bytes
    content_type: str


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class Signer:
    access_key: str
    secret_key: str
    region: str

    def headers(
        self, method: str, url: str, body: bytes, *, now: datetime | None = None
    ) -> dict[str, str]:
        """The headers that sign this request, Authorization among them."""
        moment = now or datetime.now(UTC)
        amz_date, day = moment.strftime('%Y%m%dT%H%M%SZ'), moment.strftime('%Y%m%d')
        parts = urlsplit(url)
        payload = hashlib.sha256(body).hexdigest() if body else EMPTY_HASH
        signed = {'host': parts.netloc, 'x-amz-content-sha256': payload, 'x-amz-date': amz_date}
        query = '&'.join(
            sorted(
                f'{quote(key, safe="-_.~")}={quote(value, safe="-_.~")}'
                for key, _, value in (pair.partition('=') for pair in parts.query.split('&'))
                if key
            )
        )
        names = sorted(signed)
        canonical = '\n'.join(
            [
                method,
                quote(parts.path or '/', safe='/-_.~'),
                query,
                ''.join(f'{name}:{signed[name]}\n' for name in names),
                ';'.join(names),
                payload,
            ]
        )
        scope = f'{day}/{self.region}/s3/aws4_request'
        to_sign = '\n'.join(
            ['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()]
        )
        key = _hmac(f'AWS4{self.secret_key}'.encode(), day)
        for part in (self.region, 's3', 'aws4_request'):
            key = _hmac(key, part)
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f'AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, '
            f'SignedHeaders={";".join(names)}, Signature={signature}'
        )
        return {**{k: v for k, v in signed.items() if k != 'host'}, 'Authorization': authorization}


class ObjectStore:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        signer: Signer,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base = endpoint.rstrip('/')
        self.bucket = bucket
        self.signer = signer
        self.http = httpx.AsyncClient(timeout=TIMEOUT, transport=transport)

    def _url(self, key: str = '') -> str:
        return f'{self.base}/{self.bucket}/{quote(key)}' if key else f'{self.base}/{self.bucket}'

    async def _send(
        self,
        method: str,
        url: str,
        body: bytes = b'',
        headers: dict[str, str] | None = None,
        allow: set[int] | None = None,
    ) -> httpx.Response:
        signed = self.signer.headers(method, url, body)
        try:
            with timed('storage', f'{method} {url.removeprefix(self.base)}'):
                response = await self.http.request(
                    method, url, content=body or None, headers={**(headers or {}), **signed}
                )
        except httpx.HTTPError as error:
            msg = f'{method} {url}: {error!r}'
            raise StorageError(msg) from error
        if response.is_error and response.status_code not in (allow or set()):
            msg = f'{method} {url}: {response.status_code} {response.text[:300]}'
            raise StorageError(msg)
        return response

    async def ensure_bucket(self) -> None:
        await self._send('PUT', self._url(), allow={CONFLICT})

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        await self._send('PUT', self._url(key), body, {'Content-Type': content_type})

    async def get(self, key: str) -> StoredObject | None:
        response = await self._send('GET', self._url(key), allow={NOT_FOUND})
        if response.status_code == NOT_FOUND:
            return None
        content_type = response.headers.get('content-type', 'application/octet-stream')
        return StoredObject(response.content, content_type)

    async def delete(self, key: str) -> None:
        await self._send('DELETE', self._url(key), allow={NOT_FOUND})

    async def aclose(self) -> None:
        await self.http.aclose()


def create_store(settings: Settings) -> ObjectStore:
    signer = Signer(
        settings.s3_access_key, settings.s3_secret_key.get_secret_value(), settings.s3_region
    )
    return ObjectStore(settings.s3_endpoint, settings.s3_bucket, signer)
