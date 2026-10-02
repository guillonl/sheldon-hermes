"""Socle commun des tables de l'extension dans sheldon.db.

Chaque magasin ouvre sa propre connexion au même fichier : le gateway, la commande
« hermes sheldon » et les outils de l'agent (d'autres processus) s'y croisent. Mode WAL
et délai d'attente sur verrou, comme DeviceStore, qui passe par la même ouverture.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .paths import ensure_private_dir


def open_database(path: Path, schema: str) -> sqlite3.Connection:
    """Ouvre sheldon.db (WAL, 5 s d'attente sur verrou, fichier en 0600) et pose les tables."""
    ensure_private_dir(Path(path).parent)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema)
    os.chmod(path, 0o600)
    return conn


def ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    """Ajoute à `table` les colonnes de `columns` (nom -> déclaration SQL) qui lui manquent
    encore : une base ouverte avec un schéma plus ancien que celui du code (CREATE TABLE IF
    NOT EXISTS ne touche jamais une table qui existe déjà) gagne ainsi ses colonnes neuves,
    avec leur valeur par défaut pour les lignes déjà là. Idempotent : un second appel, sur une
    base déjà à jour, ne fait rien."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, declaration in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


class SqliteStore:
    SCHEMA = ""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = open_database(self._path, self.SCHEMA)

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _page(
        self, query: str, params: Sequence[Any], before: Optional[int], limit: int
    ) -> Tuple[List[sqlite3.Row], Optional[str]]:
        """Une page, du plus récent au plus ancien selon la colonne seq.

        query finit par une clause WHERE ; next_before est le seq de la dernière ligne rendue
        quand il en reste d'autres, sinon None.
        """
        sql = query + (" AND seq < ?" if before is not None else "") + " ORDER BY seq DESC LIMIT ?"
        values = list(params) + ([before] if before is not None else []) + [limit + 1]
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        page = rows[:limit]
        next_before = str(page[-1]["seq"]) if len(rows) > limit and page else None
        return page, next_before
