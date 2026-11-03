"""The shape of anything serialized as JSON."""

from __future__ import annotations

type JSON = str | int | float | bool | list[JSON] | dict[str, JSON] | None
