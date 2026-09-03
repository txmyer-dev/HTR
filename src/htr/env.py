"""A .env loader with no dependency. Values are stripped of surrounding whitespace and a
trailing `# comment`; quotes are removed; a real environment variable always wins."""
from __future__ import annotations

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith("#"):                      # "KEY=   # comment" is an empty value
            val = ""
        elif val[:1] in ("'", '"') and val[-1:] == val[:1] and len(val) >= 2:
            val = val[1:-1]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        out[key] = val
    return out


def load_dotenv(path: str | Path | None = None) -> dict[str, str]:
    """Load <repo>/.env (or `path`) into os.environ without overriding what is already set.
    Returns what was loaded. Missing file is fine."""
    p = Path(path or os.environ.get("HTR_ENV_FILE") or Path(__file__).resolve().parents[2] / ".env")
    if not p.exists():
        return {}
    loaded = parse(p.read_text(encoding="utf-8"))
    for k, v in loaded.items():
        if k not in os.environ and v != "":
            os.environ[k] = v
    return loaded
