from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchTool:
    name: str
    provider: str
    description: str | None = None
