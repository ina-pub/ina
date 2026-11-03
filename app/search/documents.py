"""What OpenSearch holds for each link and community, and the mappings for it.

The job that keeps the index current and the rebuild script both build documents here. A link
that is removed, spam or deleted has no document: it is deleted from the index, not filtered out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infra.opensearch import Body
    from app.repos.base import Community, Link

LINKS = 'links'
COMMUNITIES = 'communities'

_LOWER = {'type': 'keyword', 'normalizer': 'lower'}
_SETTINGS: Body = {
    'number_of_shards': 1,
    'number_of_replicas': 0,
    'analysis': {'normalizer': {'lower': {'type': 'custom', 'filter': ['lowercase']}}},
}
INDICES: dict[str, Body] = {
    LINKS: {
        'settings': _SETTINGS,
        'mappings': {
            'dynamic': 'strict',
            'properties': {
                # Breaks ties between links created in the same second.
                'id': {'type': 'long'},
                'title': {'type': 'text', 'analyzer': 'english'},
                'selftext': {'type': 'text', 'analyzer': 'english'},
                'url': {'type': 'text'},
                # The domain and each domain above it, so site:example.com finds i.example.com.
                'sites': _LOWER,
                'author': _LOWER,
                'community': _LOWER,
                'community_id': {'type': 'long'},
                'flair': {'type': 'text'},
                'nsfw': {'type': 'boolean'},
                'is_self': {'type': 'boolean'},
                'created': {'type': 'date', 'format': 'epoch_second'},
                'score': {'type': 'integer'},
                'hot': {'type': 'double'},
                'comment_count': {'type': 'integer'},
            },
        },
    },
    COMMUNITIES: {
        'settings': _SETTINGS,
        'mappings': {
            'dynamic': 'strict',
            'properties': {
                'name': {'type': 'text', 'fields': {'exact': _LOWER}},
                'title': {'type': 'text', 'analyzer': 'english'},
                'description': {'type': 'text', 'analyzer': 'english'},
                'subscriber_count': {'type': 'integer'},
                'nsfw': {'type': 'boolean'},
                'type': {'type': 'keyword'},
            },
        },
    },
}


def sites(domain: str) -> list[str]:
    """``i.example.com`` → ``['i.example.com', 'example.com']``; never a bare top-level domain."""
    parts = domain.lower().removeprefix('www.').split('.')
    return ['.'.join(parts[i:]) for i in range(max(len(parts) - 1, 1))]


def searchable(link: Link) -> bool:
    return not (link.removed or link.spam or link.deleted)


def link_document(link: Link, community: Community) -> Body:
    return {
        'id': link.id,
        'title': link.title,
        'selftext': link.selftext_md,
        'url': '' if link.is_self else link.url,
        'sites': [] if link.is_self else sites(link.domain),
        'author': link.author_name.lower(),
        'community': community.name.lower(),
        'community_id': community.id,
        'flair': link.flair_text or '',
        # Only the link's own flag: searches leave out NSFW communities by id, so changing a
        # community's setting needs no reindex.
        'nsfw': link.nsfw,
        'is_self': link.is_self,
        'created': int(link.created.timestamp()),
        'score': link.score,
        'hot': link.hot_score,
        'comment_count': link.comment_count,
    }


def community_document(community: Community) -> Body:
    return {
        'name': community.name,
        'title': community.title,
        'description': community.description_md,
        'subscriber_count': community.subscriber_count,
        'nsfw': community.nsfw,
        'type': community.type,
    }
