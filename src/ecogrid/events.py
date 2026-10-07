from __future__ import annotations

import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

INTERVAL_SECONDS = 300


def interval_start(ts: int) -> int:
    return ts - ts % INTERVAL_SECONDS


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]
    # every event gets its own id so consumers can spot duplicates
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


Handler = Callable[[Event], None]


class EventBus:

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

                        self.dead_letters.append((event, repr(error)))
        finally:
            self._busy = False
