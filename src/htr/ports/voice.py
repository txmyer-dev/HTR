"""The voice vendor is a client of the core. This port covers what the core needs FROM a vendor:
parsing its webhooks into neutral shapes and verifying they are really from it. The agent's own
configuration (prompts, voices) is vendor-side and stays there."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    from_number: str
    to_number: str


@dataclass(frozen=True)
class CallEnded:
    call_id: str
    from_number: str
    to_number: str
    transcript: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class VoicePort(Protocol):
    def verify(self, secret: str | None, headers: dict[str, str], raw_body: bytes) -> bool: ...
    def parse_tool_call(self, body: dict[str, Any]) -> ToolCall: ...
    def parse_call_ended(self, body: dict[str, Any]) -> CallEnded: ...
