"""Retell webhook shapes -> neutral ToolCall / CallEnded. The only file that knows Retell's JSON;
a Vapi adapter is this file again with different key paths (SPEC R5).

Verification: shared-secret header. Retell's signed-payload scheme can replace verify() without
touching anything else."""
from __future__ import annotations

import hmac
from typing import Any

from ..ports.voice import CallEnded, ToolCall


class RetellVoice:
    def verify(self, secret: str | None, headers: dict[str, str], raw_body: bytes) -> bool:
        if not secret:            # tenant chose to run unverified (dev only)
            return True
        supplied = headers.get("x-retell-secret") or headers.get("X-Retell-Secret") or ""
        return hmac.compare_digest(supplied, secret)

    def parse_tool_call(self, body: dict[str, Any]) -> ToolCall:
        call = body.get("call", {})
        return ToolCall(
            call_id=call.get("call_id", ""),
            tool_call_id=body.get("tool_call_id") or body.get("name", "") + ":0",
            name=body.get("name", ""),
            arguments=body.get("args", {}) or {},
            from_number=call.get("from_number", ""),
            to_number=call.get("to_number", ""),
        )

    def parse_call_ended(self, body: dict[str, Any]) -> CallEnded:
        call = body.get("call", body)
        return CallEnded(
            call_id=call.get("call_id", ""),
            from_number=call.get("from_number", ""),
            to_number=call.get("to_number", ""),
            transcript=call.get("transcript", "") or "",
            metadata={k: call.get(k) for k in ("disconnection_reason", "call_analysis") if k in call},
        )
