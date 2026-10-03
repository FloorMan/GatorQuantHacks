"""Monotonic identifier allocation.

Identifiers are never reused (Section 5: session identities are never
reused; Section 9: bounded identifier use). Each prefix has its own counter,
and the counters are part of exchange state so a replay reproduces them.
"""

from collections import defaultdict


class IdAllocator:
    def __init__(self) -> None:
        self._next: dict[str, int] = defaultdict(lambda: 1)

    def allocate(self, prefix: str) -> str:
        n = self._next[prefix]
        self._next[prefix] = n + 1
        return f"{prefix}-{n:06d}"

    def observe(self, identifier: str) -> None:
        """Advance the counter past an identifier seen during replay."""
        prefix, _, num = identifier.rpartition("-")
        if prefix and num.isdigit():
            self._next[prefix] = max(self._next[prefix], int(num) + 1)

    def snapshot(self) -> dict[str, int]:
        return dict(self._next)
