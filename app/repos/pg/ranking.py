"""The ranking formulas as SQL, so a rebuild recomputes every row in one statement."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Double, Numeric, case, cast, func, literal

from app.services.ranking import CONFIDENCE_Z, HOT_EPOCH, HOT_PERIOD, HOT_PRECISION

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement


def hot(
    ups: ColumnElement[int], downs: ColumnElement[int], created: ColumnElement[object]
) -> ColumnElement[float]:
    score = ups - downs
    order = func.log(func.greatest(func.abs(score), 1))
    seconds = func.extract('epoch', created) - HOT_EPOCH
    value = func.sign(score) * order + seconds / literal(float(HOT_PERIOD))
    return cast(func.round(cast(value, Numeric), HOT_PRECISION), Double)


def controversy(ups: ColumnElement[int], downs: ColumnElement[int]) -> ColumnElement[float]:
    balance = case(
        (ups > downs, cast(downs, Double) / ups), else_=cast(ups, Double) / func.nullif(downs, 0)
    )
    return case(
        ((ups <= 0) | (downs <= 0), literal(0.0)),
        else_=func.power(cast(ups + downs, Double), balance),
    )


def confidence(ups: ColumnElement[int], downs: ColumnElement[int]) -> ColumnElement[float]:
    n = cast(ups + downs, Double)
    z = literal(CONFIDENCE_Z)
    p = cast(ups, Double) / func.nullif(n, 0)
    left = p + 1 / (2 * n) * z * z
    right = z * func.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    under = 1 + 1 / n * z * z
    return case((ups + downs == 0, literal(0.0)), else_=(left - right) / under)
