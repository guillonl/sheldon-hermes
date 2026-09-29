"""Historique des conversations, lu dans les sessions d'Hermes.

Hermes reste la seule source : chaque appareil relit ici, rien n'est recopié.
Les lignes viennent de SessionDB.get_messages(..., include_compacted=True), qui
écarte déjà les doublons laissés par la compression des conversations.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager, Dict, Iterable, List, Optional, Tuple

from . import media
from .conversations import Agent, Conversation
from .timeutil import iso_utc

logger = logging.getLogger(__name__)

SESSION_SOURCE = "sheldon"
VISIBLE_ROLES = ("user", "assistant")

MessageLoader = Callable[[str], List[Dict[str, Any]]]
MediaExtractor = Callable[[str], Tuple[List[Tuple[str, bool]], str]]


def _connect_read_only(state_db: Path) -> sqlite3.Connection:
    uri = f"{Path(state_db).absolute().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def session_key_for(conversation: Conversation, agent: Agent) -> str:
    """La clé de session qu'Hermes donne à ce chat (gateway/session.py, build_session_key).

    Le profil principal garde l'espace « main » ; un autre profil, routé par
    source.profile en mode multiplex, a le sien. Hermes réduit aussi un profil
    littéralement nommé « default » au même espace « main »
    (gateway/session.py, _session_key_namespace), même quand ce n'est pas le
    gateway lui-même qui tourne sous ce profil : un tel profil doit donc être
    rapproché de l'agent principal ici aussi.
    """
    namespace = "main" if agent.is_default or agent.id == "default" else agent.id
    return f"agent:{namespace}:{SESSION_SOURCE}:dm:{conversation.chat_id}"


def sheldon_session_ids(state_db: Path, session_key: Optional[str] = None, include_legacy: bool = False) -> List[str]:
    """Les sessions de Sheldon dans une base d'Hermes, de la plus récente à la plus ancienne.

    Sans clé : toutes les sessions de la source (comportement de l'étape 1). Avec une clé :
    celles de ce chat, plus, si include_legacy, celles sans clé, que l'étape 1 a peut-être
    laissées et qui ne peuvent venir que du chat principal.
    """
    query = "SELECT id FROM sessions WHERE source = ?"
    params: List[Any] = [SESSION_SOURCE]
    if session_key is not None:
        query += " AND (session_key = ?" + (" OR session_key IS NULL)" if include_legacy else ")")
        params.append(session_key)
    with contextlib.closing(_connect_read_only(state_db)) as conn:
        rows = conn.execute(query + " ORDER BY started_at DESC, id DESC", params).fetchall()
    return [row[0] for row in rows]


def latest_cron_session(state_db: Path, job_id: str) -> Optional[str]:
    """La dernière session d'une tâche planifiée (id « cron_<job_id>_<date>_<heure> »)."""
    if not Path(state_db).exists():
        return None
    escaped = job_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with contextlib.closing(_connect_read_only(state_db)) as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE id LIKE ? ESCAPE '\\' ORDER BY started_at DESC, id DESC LIMIT 1",
            (f"cron\\_{escaped}\\_%",),
        ).fetchone()
    return row[0] if row else None


def state_db_candidates(agent: Agent, gateway_state_db: Path) -> List[Path]:
    """Où chercher les sessions d'un agent : sa propre base, puis celle du gateway."""
    candidates = [Path(agent.home) / "state.db"] if agent.home is not None else []
    candidates.append(Path(gateway_state_db))
    return list(dict.fromkeys(path.expanduser() for path in candidates))


class SessionLocator:
    """Trouve la base d'Hermes qui tient les sessions d'une conversation.

    Selon le mode multiplex, un profil écrit peut-être dans sa propre state.db ou dans
    celle du gateway : on prend la première base qui a une session pour la clé, et on
    s'y tient le temps d'une page (les id de lignes ne se comparent pas d'une base à l'autre).
    """

    def __init__(self, candidates: Iterable[Path], session_key: str, include_legacy: bool = False) -> None:
        self._candidates = list(candidates)
        self._session_key = session_key
        self._include_legacy = include_legacy
        self.db: Optional[Path] = None

    def session_ids(self) -> List[str]:
        for db in self._candidates:
            if not db.exists():
                continue
            ids = sheldon_session_ids(db, self._session_key, self._include_legacy)
            if ids:
                self.db = db
                return ids
        self.db = None
        return []


@dataclass(frozen=True)
class HistoryPage:
    messages: List[Dict[str, Any]]
    next_before: Optional[str]


class HistoryReader:
    def __init__(
        self,
        list_session_ids: Callable[[], List[str]],
        open_sessions: Callable[[], ContextManager[MessageLoader]],
        messages_sent: Callable[[], bool] = lambda: False,
        attachment_for: Callable[[str], Optional[Dict[str, Any]]] = lambda path: None,
        extract_media: MediaExtractor = media.extract_media,
    ) -> None:
        # open_sessions ouvre la base d'Hermes le temps d'une page ; l'ordre des sessions
        # n'importe pas, les lignes sont triées par id.
        self._list_session_ids = list_session_ids
        self._open_sessions = open_sessions
        self._messages_sent = messages_sent
        self._attachment_for = attachment_for
        self._extract_media = extract_media
        self._warned_no_session = False

    def page(self, before: Optional[int], limit: int) -> HistoryPage:
        session_ids = self._list_session_ids()
        if not session_ids:
            self._warn_no_session()
            return HistoryPage([], None)
        found: List[Tuple[int, Dict[str, Any]]] = []
        # Toutes les sessions, sans s'arrêter avant : /resume rebascule le chat sur une ancienne
        # session sans changer son started_at, et ses nouveaux messages ont les id les plus hauts.
        with self._open_sessions() as load_messages:
            for session_id in session_ids:
                for row in load_messages(session_id):
                    if before is not None and int(row["id"]) >= before:
                        continue
                    message = self._to_api(row)
                    if message is not None:
                        found.append((int(row["id"]), message))
        found.sort(key=lambda pair: pair[0])
        selected = found[-limit:] if limit else []
        has_more = len(found) > len(selected)
        next_before = str(selected[0][0]) if has_more and selected else None
        return HistoryPage([message for _, message in selected], next_before)

    def _to_api(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        role = row.get("role")
        content = row.get("content")
        if role not in VISIBLE_ROLES or row.get("display_kind") or not isinstance(content, str):
            return None
        text = content
        attachments: List[Dict[str, Any]] = []
        if role == "assistant" and media.has_media(content):
            paths, text = self._extract_media(content)
            for path, _as_voice in paths:
                attachment = self._attachment_for(path)
                if attachment is not None:
                    attachments.append(attachment)
        if not text.strip() and not attachments:
            return None
        platform_id = row.get("platform_message_id")
        message: Dict[str, Any] = {
            "id": platform_id or f"h-{row['id']}",
            "role": role,
            "text": text,
            "createdAt": iso_utc(float(row["timestamp"])),
        }
        if role == "user" and isinstance(platform_id, str) and platform_id.startswith("u-"):
            message["clientMessageId"] = platform_id[2:]
        if attachments:
            message["attachments"] = attachments
        return message

    def _warn_no_session(self) -> None:
        if self._warned_no_session or not self._messages_sent():
            return
        self._warned_no_session = True
        logger.warning(
            "Sheldon: messages were sent but Hermes has no session with source %r yet; "
            "the history stays empty",
            SESSION_SOURCE,
        )
