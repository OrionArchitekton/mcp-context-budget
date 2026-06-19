from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from mcp_context_budget.tokens import estimate_tokens


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ToolRecord:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    tags: tuple[str, ...] = field(default_factory=tuple)
    profile: str = "default"

    @property
    def tool_id(self) -> str:
        return f"{self.server}/{self.name}"

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "tags": list(self.tags),
            "profile": self.profile,
        }

    @property
    def schema_text(self) -> str:
        return stable_json(self.canonical_payload)

    @property
    def schema_tokens(self) -> int:
        return estimate_tokens(self.schema_text)

    @property
    def schema_hash(self) -> str:
        return hashlib.sha256(self.schema_text.encode("utf-8")).hexdigest()

    @property
    def search_text(self) -> str:
        parts = [self.server, self.name, self.description, self.profile, *self.tags]
        return " ".join(p for p in parts if p)
