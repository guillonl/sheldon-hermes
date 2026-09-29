"""Diffusion des événements vers les WebSockets ouverts.

Une file bornée par abonné : un appareil lent perd les plus vieux événements
plutôt que de ralentir Hermes. L'app recharge l'historique à la reconnexion.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Set


class EventHub:
    def __init__(self, max_queue: int = 256) -> None:
        self._max_queue = max_queue
        self._queues: Set[asyncio.Queue] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    def publish(self, event: Dict[str, Any]) -> None:
        for queue in list(self._queues):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
