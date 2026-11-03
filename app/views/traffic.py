"""``/in/{name}/about/traffic``: r2's traffic tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.views.communities import CommunityPage

if TYPE_CHECKING:
    from app.jsontypes import JSON
    from app.services.traffic import Row


@dataclass(frozen=True, kw_only=True)
class TrafficPage(CommunityPage):
    template: ClassVar[str] = 'traffic.html'
    cacheable: ClassVar[bool] = False

    hours: tuple[Row, ...]
    days: tuple[Row, ...]
    months: tuple[Row, ...]

    @property
    def title(self) -> str:
        return f'traffic stats for {self.community.name}'

    @staticmethod
    def most(rows: tuple[Row, ...]) -> int:
        """The busiest period's page views, for scaling each row's bar."""
        return max((row.pageviews for row in rows), default=0) or 1

    def to_json(self) -> JSON:
        """As r2's: ``[timestamp, uniques, pageviews]``, with subscriptions for days."""
        return {
            'hour': [[int(r.start.timestamp()), r.uniques, r.pageviews] for r in self.hours],
            'day': [
                [int(r.start.timestamp()), r.uniques, r.pageviews, r.subscriptions]
                for r in self.days
            ],
            'month': [[int(r.start.timestamp()), r.uniques, r.pageviews] for r in self.months],
        }
