"""Fetching what users link to, without letting a link reach inside our own network.

Every URL, and every redirect, must be http or https on a host whose addresses are all public.
The request then goes to the address that was checked, not to a fresh lookup of the name, so a
name that changes its answer between the check and the request (DNS rebinding) gets nowhere;
the TLS certificate is still checked against the name. Bodies stop at a size limit.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

TIMEOUT = httpx.Timeout(5.0)
MAX_REDIRECTS = 5
USER_AGENT = 'ina-media-fetcher/1.0'
REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """The URL could not be fetched, or may not be."""


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    content_type: str
    body: bytes


def _public(address: str) -> bool:
    return ipaddress.ip_address(address.split('%', maxsplit=1)[0]).is_global


class Fetcher:
    def __init__(
        self, *, allow_private: bool = False, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        # Tests fetch from a server on this machine; nothing else should.
        self.allow_private = allow_private
        self.http = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, transport=transport)

    async def _address(self, host: str, port: int) -> str:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except OSError as error:
            msg = f'cannot resolve {host}'
            raise FetchError(msg) from error
        addresses = [str(info[4][0]) for info in infos]
        if not addresses:
            msg = f'no address for {host}'
            raise FetchError(msg)
        if not self.allow_private and not all(_public(address) for address in addresses):
            msg = f'{host} is not a public address'
            raise FetchError(msg)
        return addresses[0]

    async def _one(
        self, url: str, limit: int, *, read: bool = True
    ) -> tuple[httpx.Response, bytes]:
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname:
            msg = f'not an http url: {url}'
            raise FetchError(msg)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        address = await self._address(parts.hostname, port)
        host = f'[{address}]' if ':' in address else address
        pinned = urlunsplit((parts.scheme, f'{host}:{port}', parts.path or '/', parts.query, ''))
        request = self.http.build_request(
            'GET',
            pinned,
            headers={'Host': parts.netloc, 'User-Agent': USER_AGENT},
            extensions={'sni_hostname': parts.hostname},
        )
        try:
            response = await self.http.send(request, stream=True)
            try:
                body = bytearray()
                # A page that is only being located is not read.
                if read:
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > limit:
                            msg = f'{url} is larger than {limit} bytes'
                            raise FetchError(msg)
            finally:
                await response.aclose()
        except httpx.HTTPError as error:
            msg = f'{url}: {error!r}'
            raise FetchError(msg) from error
        return response, bytes(body)

    async def _follow(
        self, url: str, limit: int, *, read: bool
    ) -> tuple[str, httpx.Response, bytes]:
        for _ in range(MAX_REDIRECTS + 1):
            response, body = await self._one(url, limit, read=read)
            if response.status_code in REDIRECTS and 'location' in response.headers:
                url = urljoin(url, response.headers['location'])
                continue
            if response.is_error and read:
                msg = f'{url}: {response.status_code}'
                raise FetchError(msg)
            return url, response, body
        msg = f'too many redirects from {url}'
        raise FetchError(msg)

    async def get(self, url: str, *, limit: int) -> Fetched:
        """GET ``url``, following up to five redirects, each checked as the first was."""
        url, response, body = await self._follow(url, limit, read=True)
        content_type = response.headers.get('content-type', '').split(';')[0].strip()
        return Fetched(url, content_type.lower(), body)

    async def locate(self, url: str) -> str:
        """Where ``url`` leads after its redirects, without reading the page found there.

        The last answer may be an error: sites often refuse fetchers like this one, but where
        they were sent is already known.
        """
        return (await self._follow(url, 0, read=False))[0]

    async def aclose(self) -> None:
        await self.http.aclose()
