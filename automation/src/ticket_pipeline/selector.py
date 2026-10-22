"""Ticket-id extraction and regression target selection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Sequence

# Ticket numbers in this project are ``ORS-<id>``. A bare ``#123`` is a GitHub
# issue reference, not a ticket, so it must never trigger a ticket lookup.
_TICKET_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_])ORS[-_ ]?(\d+)\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_])ticket(?:_id)?[\s:#_-]*(\d+)\b", re.IGNORECASE),
    re.compile(r"工单[\s:#_-]*(\d+)\b"),
)


def extract_ticket_ids(text: str) -> List[int]:
    """Extract ticket ids from commit messages or PR descriptions.

    Matches are ordered by their position in the source text, not by the
    pattern definition order.
    """
    matches = []
    for pattern in _TICKET_PATTERNS:
        for match in pattern.finditer(text or ""):
            matches.append((match.start(), int(match.group(1))))

    found: List[int] = []
    seen = set()
    for _, ticket_id in sorted(matches, key=lambda item: item[0]):
        if ticket_id not in seen:
            seen.add(ticket_id)
            found.append(ticket_id)
    return found


def select_promoted_tests(
    ticket_ids: Iterable[int],
    promoted_root: Path,
) -> List[str]:
    """Return generated test files for promoted ticket ids."""
    targets: List[str] = []
    for ticket_id in ticket_ids:
        ticket_dir = Path(promoted_root) / str(ticket_id)
        if not ticket_dir.is_dir():
            continue
        preferred = ticket_dir / "test_gen.py"
        if preferred.is_file():
            targets.append(str(preferred))
            continue
        targets.extend(str(path) for path in sorted(ticket_dir.glob("test_*.py")))
    return targets


def select_regression_targets(
    text: str,
    promoted_root: Path,
    regression_targets: Sequence[str],
) -> List[str]:
    """Select ticket-specific tests and always append regression targets."""
    ticket_targets = select_promoted_tests(extract_ticket_ids(text), promoted_root)
    ordered = []
    for item in [*ticket_targets, *regression_targets]:
        if item not in ordered:
            ordered.append(item)
    return ordered