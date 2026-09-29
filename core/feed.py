"""Le fil : ce qu'Hermes a fait sans qu'on lui demande, une carte par tâche planifiée livrée.

Une tâche planifiée (cron) livrée à Sheldon arrive par adapter.send avec
metadata["job_id"], et un texte enveloppé par Hermes (« Cronjob Response: <nom> »,
sauf cron.wrap_response: false). Hermes ne la recopie pas dans l'historique du chat
(cron.mirror_delivery est coupé par défaut) : la carte du fil garde donc le texte
entier. Chaque carte a sa propre conversation, « feed-<id> » : la page « Voir » de l'app,
qui commence par ce texte et où Léo peut demander une suite à Hermes. Les actions de la
tâche se relisent dans sa session de state.db (appels d'outils, raisonnement) : c'est
Hermes qui reste la source.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .media import first_line
from .sqlite import SqliteStore
from .timeutil import iso_utc

_CRON_HEADER = re.compile(r"\ACronjob Response: (?P<name>[^\n]*)\n\(job_id: (?P<job>[^)\n]*)\)\n-+\n\n?")
_CRON_FOOTER = re.compile(r"\n*To stop or manage this job, send me a new message \(e\.g\. \"stop reminder [^\n]*\"\)\.\s*\Z")
_PREVIEW_KEYS = ("command", "query", "url", "path", "file_path", "title", "name", "subject", "to")
MAX_STEPS = 100
THREAD_PREFIX = "feed-"
MAX_REASONING = 2000
PREVIEW_LENGTH = 80


def parse_cron_delivery(text: str) -> Tuple[Optional[str], Optional[str], str]:
    """(nom de la tâche, job_id, texte sans l'enveloppe d'Hermes)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    header = _CRON_HEADER.match(normalized)
    if header is None:
        return None, None, normalized
    body = _CRON_FOOTER.sub("", normalized[header.end():])
    return header.group("name").strip() or None, header.group("job").strip() or None, body.strip()


@dataclass(frozen=True)
class FeedItem:
    id: str
    kind: str
    # La conversation que visait la tâche (--deliver sheldon ou sheldon:<conversation>).
    conversation_id: str
    agent_id: str
    title: str
    summary: str
    text: str
    job_id: Optional[str]
    session_id: Optional[str]
    created_at: float
    seq: int
    file_ids: List[str] = field(default_factory=list)

    @property
    def thread_id(self) -> str:
        return THREAD_PREFIX + self.id

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "conversationId": self.thread_id,
            "deliveredTo": self.conversation_id,
            "agentId": self.agent_id,
            "title": self.title,
            "summary": self.summary,
            "createdAt": iso_utc(self.created_at),
            "jobId": self.job_id,
        }


_COLUMNS = "seq, id, kind, conversation_id, agent_id, title, summary, text, job_id, session_id, created_at, files"


def _item(row: Optional[sqlite3.Row]) -> Optional[FeedItem]:
    if row is None:
        return None
    return FeedItem(
        row["id"], row["kind"], row["conversation_id"], row["agent_id"], row["title"], row["summary"],
        row["text"], row["job_id"], row["session_id"], row["created_at"], row["seq"], json.loads(row["files"]),
    )


class FeedStore(SqliteStore):
    SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_items (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    text TEXT NOT NULL,
    job_id TEXT,
    session_id TEXT,
    created_at REAL NOT NULL,
    files TEXT NOT NULL DEFAULT '[]'
);
"""

    def add(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        title: str,
        text: str,
        job_id: Optional[str],
        session_id: Optional[str],
        kind: str = "task",
        file_ids: Sequence[str] = (),
    ) -> FeedItem:
        item_id = f"f-{uuid.uuid4().hex}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO feed_items (id, kind, conversation_id, agent_id, title, summary, text, job_id, "
                "session_id, created_at, files) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, kind, conversation_id, agent_id, title, first_line(text), text, job_id, session_id,
                 self._clock(), json.dumps(list(file_ids))),
            )
        item = self.get(item_id)
        assert item is not None
        return item

    def attach_file(self, item_id: str, file_id: str) -> Optional[FeedItem]:
        """Joint un fichier reçu après coup à une carte déjà livrée (constat Important 2,
        relecture du lot 12-13) : Hermes 0.20.4 envoie chaque fichier d'un cron à part
        (send_image_file), sans le job_id du texte."""
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM feed_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            files = json.loads(row["files"])
            if file_id not in files:
                files.append(file_id)
                self._conn.execute("UPDATE feed_items SET files = ? WHERE id = ?", (json.dumps(files), item_id))
        return self.get(item_id)

    def for_thread(self, conversation_id: object) -> Optional[FeedItem]:
        """La carte d'une conversation « feed-<id> », ou None."""
        if not isinstance(conversation_id, str) or not conversation_id.startswith(THREAD_PREFIX):
            return None
        return self.get(conversation_id[len(THREAD_PREFIX):])

    def get(self, item_id: str) -> Optional[FeedItem]:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM feed_items WHERE id = ?", (item_id,)).fetchone()
        return _item(row)

    def page(self, before: Optional[int], limit: int) -> Tuple[List[FeedItem], Optional[str]]:
        rows, next_before = self._page(f"SELECT {_COLUMNS} FROM feed_items WHERE 1 = 1", [], before, limit)
        return [_item(row) for row in rows], next_before

    def latest(self, conversation_id: str) -> Optional[FeedItem]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM feed_items WHERE conversation_id = ? ORDER BY seq DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return _item(row)


# La page « Voir » : les actions d'une session d'Hermes, lues dans ses messages.


def _arguments(call: Dict[str, Any]) -> Dict[str, Any]:
    raw = (call.get("function") or {}).get("arguments", call.get("arguments"))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _tool_name(call: Dict[str, Any]) -> str:
    return str((call.get("function") or {}).get("name") or call.get("name") or "tool")


def tool_label(name: str, args: Dict[str, Any], redact: Callable[[str], str] = lambda text: text) -> str:
    preview = next((args[key] for key in _PREVIEW_KEYS if isinstance(args.get(key), str) and args[key].strip()), None)
    if preview is None:
        preview = next((value for value in args.values() if isinstance(value, str) and value.strip()), None)
    if preview is None:
        return name
    preview = " ".join(redact(preview).split())
    if len(preview) > PREVIEW_LENGTH:
        preview = preview[: PREVIEW_LENGTH - 1].rstrip() + "…"
    return f"{name}: {preview}"


def _failed(content: Any) -> bool:
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("error"):
                return True
        return stripped.startswith(("Error", "Tool execution failed"))
    return False


def build_steps(rows: List[Dict[str, Any]], redact: Callable[[str], str] = lambda text: text) -> List[Dict[str, Any]]:
    results = {row.get("tool_call_id"): row for row in rows if row.get("role") == "tool"}
    steps: List[Dict[str, Any]] = []
    for row in rows:
        calls = row.get("tool_calls")
        if row.get("role") != "assistant" or not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            result = results.get(call.get("id"))
            steps.append({
                "tool": _tool_name(call),
                "label": tool_label(_tool_name(call), _arguments(call), redact),
                "status": "error" if result is not None and _failed(result.get("content")) else "ok",
                "createdAt": iso_utc(float(row["timestamp"])),
            })
    return steps[-MAX_STEPS:]


def last_reasoning(rows: List[Dict[str, Any]], redact: Callable[[str], str] = lambda text: text) -> Optional[str]:
    """Le dernier raisonnement, masqué comme les pas (redact : celui d'Hermes, revue finale M21)."""
    for row in reversed(rows):
        reasoning = row.get("reasoning") if row.get("role") == "assistant" else None
        if isinstance(reasoning, str) and reasoning.strip():
            text = redact(reasoning).strip()
            return text if len(text) <= MAX_REASONING else text[: MAX_REASONING - 1].rstrip() + "…"
    return None
