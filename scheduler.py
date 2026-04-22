"""Pure schedule parsing + slot utilities. No Telegram imports here."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time


@dataclass
class Slot:
    hour: int
    minute: int
    topic: str

    def to_dict(self) -> dict:
        return {"hour": self.hour, "minute": self.minute, "topic": self.topic}

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        return cls(hour=d["hour"], minute=d["minute"], topic=d["topic"])

    def label(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d} {self.topic}"


class ParseError(Exception):
    pass


_LINE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s+(.+)$")


def parse_schedule(raw_body: str) -> tuple[list[dict], str]:
    """Parse multi-line schedule body into (slot dicts, normalised raw string).

    Skips blank lines. Raises ParseError on first bad line (1-indexed position among
    non-blank lines).
    """
    slots: list[dict] = []
    kept_lines: list[str] = []
    line_no = 0
    for raw in raw_body.splitlines():
        line = raw.strip()
        if not line:
            continue
        line_no += 1
        m = _LINE_RE.match(line)
        if not m:
            raise ParseError(f"Could not parse line {line_no}: `{line}`")
        hour = int(m.group(1))
        minute = int(m.group(2))
        topic = m.group(3).strip()
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ParseError(f"Invalid time on line {line_no}: `{line}`")
        slots.append({"hour": hour, "minute": minute, "topic": topic})
        kept_lines.append(f"{hour:02d}:{minute:02d} {topic}")

    if not slots:
        raise ParseError("Schedule is empty — provide at least one `HH:MM Topic` line.")

    return slots, "\n".join(kept_lines)


def next_upcoming_slot(slots: list[dict], now: datetime) -> dict | None:
    """First slot whose HH:MM today is >= now. None if all are past."""
    now_t = now.time()
    upcoming = [s for s in slots if time(s["hour"], s["minute"]) >= now_t]
    if not upcoming:
        return None
    upcoming.sort(key=lambda s: (s["hour"], s["minute"]))
    return upcoming[0]


def format_slot(slot: dict | None) -> str:
    if slot is None:
        return "—"
    return f"{slot['hour']:02d}:{slot['minute']:02d} {slot['topic']}"
