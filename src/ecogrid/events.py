"""Shared kernel - the bits all three contexts agree on.

Keep this file SMALL. Anything added here becomes something all three of us have to
agree on before changing, so if it only matters to one context, put it there instead.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# 5 min trading intervals. We picked this to line up with the NEM's 5-minute settlement
# so the numbers would make sense if this was ever plugged into a real retailer.
INTERVAL_SECONDS = 300


def interval_start(ts: int) -> int:
    # e.g. 12:03:40 -> 12:00:00. Works because epoch time is aligned to 5 mins anyway.
    return ts - ts % INTERVAL_SECONDS


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]
    # every event gets its own id so consumers can spot duplicates (see SettlementService._once)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


Handler = Callable[[Event], None]


class EventBus:
    """Fake Kafka that runs in memory, good enough for the prototype + tests.

    duplicate_every lets us deliberately send every Nth event twice. Real Kafka is
    at-least-once, so the consumers have to cope with repeats - this is how we test that.
    """

    def __init__(self, duplicate_every: int = 0) -> None:
        self.duplicate_every = duplicate_every
        self.published: list[Event] = []
        self.dead_letters: list[tuple[Event, str]] = []
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._queue: deque[Event] = deque()
        self._busy = False

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.published.append(event)
        self._queue.append(event)
        if self.duplicate_every and len(self.published) % self.duplicate_every == 0:
            self._queue.append(event)  # simulated re-delivery
        # Handlers often publish new events themselves. Without the _busy flag we'd
        # recurse and events could arrive out of order, so nested publishes just queue up.
        if not self._busy:
            self._deliver_all()

    def _deliver_all(self) -> None:
        self._busy = True
        try:
            while self._queue:
                event = self._queue.popleft()
                for handler in self._handlers[event.type]:
                    try:
                        handler(event)
                    except Exception as error:
                        # One bad event shouldn't take down everyone else's handlers.
                        # Park it so someone can look at it later (dead letter queue).
                        self.dead_letters.append((event, repr(error)))
        finally:
            self._busy = False
