"""The committed-text rule (STEALS: capture, the discipline rather than the code). Everything
durable is appended to JSONL under HTR_DATA_DIR; the database is derived state. rulings.jsonl
is the correction log: every ruling, every edit, every outcome, in the owner's own words."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    return Path(os.environ.get("HTR_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))


def append(stream: str, record: dict[str, Any]) -> None:
    """Append one record to <data_dir>/<stream>.jsonl. Never raises: a full disk must not stop a
    ruling from being applied; the database still has it."""
    try:
        d = data_dir()
        d.mkdir(parents=True, exist_ok=True)
        rec = {"at": datetime.now(timezone.utc).isoformat(), **record}
        with (d / f"{stream}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
