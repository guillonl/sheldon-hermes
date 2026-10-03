"""Les constats gardés pour Hermes jusqu'au prochain message de l'app (spec 3.2).

Ce que l'extension sait seule (une proposition expirée sans réponse) n'ouvre jamais un tour :
le constat attend ici, dans sheldon.db, et part avec le prochain message de l'utilisateur dans
la même conversation, dans la note [Sheldon] de ce message (core/turn_context.py). Borné : cinq
par conversation, sept jours au plus.
"""
from __future__ import annotations

from typing import List

from .sqlite import SqliteStore


class NoticeStore(SqliteStore):
    MAX_PER_CONVERSATION = 5
    TTL_SECONDS = 7 * 24 * 3600
    SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS notices_by_conversation ON notices(conversation_id, seq);
"""

    def add(self, conversation_id: str, text: str) -> None:
        """Un constat de plus pour cette conversation ; au-delà des cinq plus récents, les plus
        anciens partent."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO notices (conversation_id, text, created_at) VALUES (?, ?, ?)",
                (conversation_id, text, self._clock()),
            )
            self._conn.execute(
                "DELETE FROM notices WHERE conversation_id = ? AND seq NOT IN "
                "(SELECT seq FROM notices WHERE conversation_id = ? ORDER BY seq DESC LIMIT ?)",
                (conversation_id, conversation_id, self.MAX_PER_CONVERSATION),
            )

    def take(self, conversation_id: str, limit: int = MAX_PER_CONVERSATION) -> List[str]:
        """Au plus `limit` constats de cette conversation, les plus anciens d'abord, sans ceux de
        plus d'une semaine, qui partent ; ceux qui sont pris sont effacés (chacun ne part qu'une
        fois), les autres attendent le message suivant : une note pleine n'en perd aucun."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM notices WHERE conversation_id = ? AND created_at <= ?",
                (conversation_id, self._clock() - self.TTL_SECONDS),
            )
            rows = self._conn.execute(
                "SELECT seq, text FROM notices WHERE conversation_id = ? ORDER BY seq LIMIT ?",
                (conversation_id, max(limit, 0)),
            ).fetchall()
            self._conn.executemany("DELETE FROM notices WHERE seq = ?", [(row["seq"],) for row in rows])
        return [row["text"] for row in rows]
