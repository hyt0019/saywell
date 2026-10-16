from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class SessionGate:
    """UI-independent guard against cancelled, stale or duplicated completions."""

    generation: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    inserted: bool = False

    def begin(self) -> tuple[int, threading.Event]:
        self.cancel_event.set()
        self.generation += 1
        self.cancel_event = threading.Event()
        self.inserted = False
        return self.generation, self.cancel_event

    def cancel(self):
        self.cancel_event.set()
        self.generation += 1

    def accepts(self, generation: int) -> bool:
        return generation == self.generation and not self.cancel_event.is_set()

    def claim_insert(self, generation: int) -> bool:
        if not self.accepts(generation) or self.inserted:
            return False
        self.inserted = True
        return True
