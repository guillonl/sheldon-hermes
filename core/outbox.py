"""Le canal entre les processus d'Hermes et le gateway, par une table de sheldon.db.

Les outils de l'agent et les envois hors du gateway (desktop, « hermes cron run »)
tournent loin du serveur de Sheldon : ils rangent ce qu'ils ont fait dans sa table,
puis ajoutent une ligne ici. Le gateway relit cette table toutes les 0,5 s et publie.
Une ligne n'est relayée qu'une fois, même si le gateway redémarre entre-temps, sauf si
le handler est interrompu en cours de route (annulation) : la ligne n'est alors pas
marquée et sera rejouée au tour suivant, la livraison devenant « au moins une fois ».
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from .sqlite import SqliteStore

logger = logging.getLogger(__name__)

STARTUP_MAX_AGE = 600.0
KEEP_SECONDS = 86400.0
PURGE_EVERY = 3600.0

Handler = Callable[[Dict[str, Any]], Union[None, Awaitable[None]]]


def append_row(conn: Any, entry_type: str, payload: Dict[str, Any], created_at: float) -> int:
    """Une ligne de l'outbox sur une connexion donnée : dans la transaction de l'appelant quand
    il en tient une (CallStore.place : l'appel et sa ligne sont écrits ensemble ou pas du tout)."""
    cursor = conn.execute(
        "INSERT INTO outbox (type, payload, created_at) VALUES (?, ?, ?)",
        (entry_type, json.dumps(payload, ensure_ascii=False), created_at),
    )
    return int(cursor.lastrowid)


@dataclass(frozen=True)
class OutboxEntry:
    id: int
    type: str
    payload: Dict[str, Any]
    created_at: float


class Outbox(SqliteStore):
    SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    relayed_at REAL
);
CREATE INDEX IF NOT EXISTS outbox_waiting ON outbox(relayed_at, id);
"""

    def append(self, entry_type: str, payload: Dict[str, Any]) -> int:
        with self._lock:
            return append_row(self._conn, entry_type, payload, self._clock())

    def waiting(self, limit: int = 100) -> List[OutboxEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, type, payload, created_at FROM outbox WHERE relayed_at IS NULL ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [OutboxEntry(r["id"], r["type"], json.loads(r["payload"]), r["created_at"]) for r in rows]

    def mark_relayed(self, entry_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE outbox SET relayed_at = ? WHERE id = ?", (self._clock(), entry_id))

    def drop_older_than(self, max_age: float) -> int:
        """Au démarrage : ce qui attend depuis trop longtemps ne vaut plus une notification."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE outbox SET relayed_at = ? WHERE relayed_at IS NULL AND created_at < ?",
                (self._clock(), self._clock() - max_age),
            )
            return cursor.rowcount

    def purge(self, keep_seconds: float = KEEP_SECONDS) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM outbox WHERE relayed_at IS NOT NULL AND relayed_at < ?", (self._clock() - keep_seconds,)
            )
            return cursor.rowcount


class OutboxRelay:
    def __init__(
        self,
        outbox: Outbox,
        handlers: Dict[str, Handler],
        tick: Callable[[], Any] = lambda: None,
        interval: float = 0.5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # tick : appelé à chaque tour (les demandes arrivées à échéance expirent là).
        self._outbox = outbox
        self._handlers = handlers
        self._tick = tick
        self._interval = interval
        self._clock = clock
        self._last_purge: Optional[float] = None

    async def run_once(self) -> int:
        entries = self._outbox.waiting()
        for entry in entries:
            handler = self._handlers.get(entry.type)
            try:
                if handler is None:
                    logger.warning("Sheldon: no handler for outbox entry %r", entry.type)
                else:
                    result = handler(entry.payload)
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                # Relayée quand même : une erreur ne doit pas se répéter à chaque tour.
                logger.exception("Sheldon: outbox entry %s (%s) failed", entry.id, entry.type)
                self._outbox.mark_relayed(entry.id)
            except BaseException:
                # Annulation (ou autre interruption) pendant le handler : la ligne n'a pas
                # fini son travail, elle n'est pas marquée et sera rejouée au tour suivant.
                logger.info("Sheldon: outbox entry %s (%s) interrupted, will be replayed", entry.id, entry.type)
                raise
            else:
                self._outbox.mark_relayed(entry.id)
        try:
            self._tick()
        except Exception:
            logger.exception("Sheldon: periodic check failed")
        now = self._clock()
        if self._last_purge is None or now - self._last_purge >= PURGE_EVERY:
            self._outbox.purge()
            self._last_purge = now
        return len(entries)

    async def run(self) -> None:
        try:
            self._outbox.drop_older_than(STARTUP_MAX_AGE)
        except Exception:
            # Base verrouillée plus de 5 s, erreur d'E/S : le relais part quand même. Sans ceci,
            # la tâche mourait sur cette exception pour toute la vie du gateway (revue finale, I2).
            logger.exception("Sheldon: outbox startup cleanup failed, relaying anyway")
        while True:
            try:
                await self.run_once()
            except Exception:
                # Base verrouillée plus de 5 s (une commande « hermes sheldon ») : on réessaie.
                logger.exception("Sheldon: outbox relay failed, retrying")
            await asyncio.sleep(self._interval)
