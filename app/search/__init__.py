"""Search: OpenSearch documents built from Postgres, and r2's query syntax."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.infra.opensearch import SearchClient
from app.search.documents import INDICES

if TYPE_CHECKING:
    from app.settings import Settings


def create_search(settings: Settings) -> SearchClient:
    return SearchClient(
        settings.opensearch_url,
        prefix=settings.search_prefix,
        indices=INDICES,
        refresh=settings.search_refresh,
    )
