"""reddit's ranking formulas, as r2's ``_sorts.pyx`` computes them.

``hot`` is reproduced in SQL by ``app.repos.pg.ranking`` so a rebuild can run as one UPDATE; a
test keeps the two in step.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

# r2's epoch for hot: 2005-12-08 07:46:43 UTC.
HOT_EPOCH = 1134028003
HOT_PERIOD = 45000
HOT_PRECISION = 7


def hot(ups: int, downs: int, created: datetime) -> float:
    score = ups - downs
    order = math.log10(max(abs(score), 1))
    sign = (score > 0) - (score < 0)
    seconds = created.timestamp() - HOT_EPOCH
    return round(sign * order + seconds / HOT_PERIOD, HOT_PRECISION)


def controversy(ups: int, downs: int) -> float:
    if ups <= 0 or downs <= 0:
        return 0.0
    balance = downs / ups if ups > downs else ups / downs
    return float((ups + downs) ** balance)


# r2's z for an 80% confidence interval.
CONFIDENCE_Z = 1.281551565545


def confidence(ups: int, downs: int) -> float:
    """The lower bound of the Wilson score interval, r2's "best" comment sort."""
    n = ups + downs
    if n == 0:
        return 0.0
    z = CONFIDENCE_Z
    p = ups / n
    left = p + 1 / (2 * n) * z * z
    right = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    under = 1 + 1 / n * z * z
    return (left - right) / under


# r2's live-config thresholds for the controversial dagger.
CONTROVERSIAL_MIN_VOTES = 7
CONTROVERSIAL_RATIO = (0.4, 0.6)


def is_controversial(ups: int, downs: int) -> bool:
    votes = ups + downs
    if votes < CONTROVERSIAL_MIN_VOTES:
        return False
    low, high = CONTROVERSIAL_RATIO
    return low <= ups / votes <= high
