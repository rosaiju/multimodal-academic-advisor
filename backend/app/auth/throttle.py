"""Slowing down password guessing.

Twelve failed logins took 0.6 seconds before this existed, which is a working
brute-force rate. This adds a cooldown after a handful of failures, with the
delay doubling each time and capped, so guessing becomes slow without anyone ever
being locked out permanently.

**What the counter is keyed on, and why it is not one of the obvious choices.**

Keyed on the EMAIL alone, anyone could lock any student out of their own account
by failing a few logins against their address on purpose. A defence that hands an
attacker a denial of service is worse than none.

Keyed on the IP alone, one careless person in a computer lab would lock out
everybody sharing that address - and a university NAT means that could be a whole
building.

So it is keyed on the pair. A guessing run against one account from one machine
is throttled; a student on the same network, or the same student on a different
account, is unaffected.

The residual gap is password SPRAYING: one guess each against many different
addresses from one address never trips a per-pair counter. Closing that needs a
per-IP budget too, which on a shared campus NAT risks the lockout-a-building
problem above. It is a real limitation, noted rather than papered over.

**Failures are counted whether or not the account exists.** Throttling only real
accounts would make a 429 mean "this address is registered", which is exactly the
account oracle that `/auth/login` is careful not to be.

State is in memory, so it is per-process: a restart forgives everyone, and two
workers each keep their own tally. For a single-process deployment that is
accurate; behind a load balancer it would need shared storage.

*** NO LLM CODE. ***
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Attempts:
    """One (client, account) pair's recent history."""

    failures: int = 0
    #: How many cooldowns this pair has already served. Drives the backoff.
    lockouts: int = 0
    #: Monotonic time the current cooldown ends; 0 when not locked out.
    locked_until: float = 0.0
    last_seen: float = 0.0


@dataclass
class Decision:
    """Whether a login attempt may proceed."""

    allowed: bool
    #: Whole seconds the caller must wait. 0 when allowed.
    retry_after: int = 0


@dataclass
class LoginThrottle:
    """Counts failed logins per (client, account) and imposes a cooldown.

    `clock` is injectable so the tests can advance time instead of sleeping - a
    test suite that waits out a real cooldown is a test suite people stop running.
    """

    max_failures: int = 5
    base_cooldown_seconds: int = 30
    max_cooldown_seconds: int = 900
    #: A pair idle this long is forgotten entirely. This is what makes the lockout
    #: temporary in the strongest sense: the state itself expires.
    forget_after_seconds: int = 900
    clock: object = time.monotonic

    _entries: dict[tuple[str, str], _Attempts] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def _now(self) -> float:
        return self.clock()  # type: ignore[operator]

    def _prune(self, now: float) -> None:
        """Drop pairs nobody has touched recently, so the dict cannot grow forever."""
        stale = [
            key
            for key, entry in self._entries.items()
            if now - entry.last_seen > self.forget_after_seconds and now >= entry.locked_until
        ]
        for key in stale:
            del self._entries[key]

    def check(self, client: str, email: str) -> Decision:
        """May this pair attempt a login right now?"""
        now = self._now()
        with self._lock:
            self._prune(now)
            entry = self._entries.get((client, email))
            if entry is None or now >= entry.locked_until:
                return Decision(allowed=True)
            return Decision(allowed=False, retry_after=max(1, int(entry.locked_until - now)))

    def record_failure(self, client: str, email: str) -> Decision:
        """Count a failed attempt, starting a cooldown once the limit is reached.

        Returns the decision that now applies, so the caller can report the wait
        on the very attempt that triggered it rather than on the next one.
        """
        now = self._now()
        with self._lock:
            entry = self._entries.setdefault((client, email), _Attempts())
            entry.last_seen = now
            entry.failures += 1

            if entry.failures < self.max_failures:
                return Decision(allowed=True)

            # Double each time, capped. Capped rather than unbounded because an
            # ever-growing delay becomes a permanent lockout in everything but name.
            cooldown = min(
                self.base_cooldown_seconds * (2**entry.lockouts),
                self.max_cooldown_seconds,
            )
            entry.lockouts += 1
            entry.failures = 0
            entry.locked_until = now + cooldown
            return Decision(allowed=False, retry_after=int(cooldown))

    def record_success(self, client: str, email: str) -> None:
        """Forget this pair. A correct password is proof the guessing has stopped."""
        with self._lock:
            self._entries.pop((client, email), None)

    def reset(self) -> None:
        """Drop all state. For tests, and for an operator who needs to clear a jam."""
        with self._lock:
            self._entries.clear()


#: One throttle for the process. Module-level because it is per-process state by
#: design; `reset()` keeps tests independent of each other.
login_throttle = LoginThrottle()
