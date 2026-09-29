"""Les conversations de Sheldon : le chat principal, un chat par agent, des chats de sujet.

Hermes voit chaque conversation comme un chat privé à part (un chat_id, donc une session
à part). Le chat principal garde le chat_id « owner » de l'étape 1 pour retrouver son
historique. Les agents sont les profils d'Hermes ; les chats de sujet sont rangés ici,
dans sheldon.db, pour que la commande, le gateway et les outils de l'agent les voient.
"""
from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import SheldonError
from .sqlite import SqliteStore
from .text import clean_line

MAIN_ID = "main"
MAIN_CHAT_ID = "owner"
AGENT_PREFIX = "agent-"
MAX_TITLE_LENGTH = 60
_SLUG_LENGTH = 32
_TOPIC_FALLBACK = "sujet"


@dataclass(frozen=True)
class Agent:
    """Un profil d'Hermes, vu par l'app."""

    id: str
    name: str
    description: str = ""
    model: Optional[str] = None
    # L'agent du chat principal : le profil qui fait tourner le gateway.
    is_default: bool = False
    # Dossier du profil (sa state.db), None s'il est inconnu.
    home: Optional[Path] = None

    def to_json(self, conversation_id: str) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "model": self.model,
            "isDefault": self.is_default,
            "conversationId": conversation_id,
        }


FALLBACK_AGENT = Agent("default", "Hermes", is_default=True)


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    kind: str
    agent_id: str
    chat_id: str

    def to_json(self, last_message: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "agentId": self.agent_id,
            "lastMessage": last_message,
        }


class ConversationError(SheldonError):
    pass


def slugify(title: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:_SLUG_LENGTH].strip("-")


class ConversationCatalog(SqliteStore):
    SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

    def __init__(
        self,
        path: Path,
        agents: Callable[[], List[Agent]],
        multiplex: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(path, clock)
        self._agents = agents
        self._multiplex = multiplex

    def multiplex(self) -> bool:
        return bool(self._multiplex())

    def agents(self) -> List[Agent]:
        return list(self._agents()) or [FALLBACK_AGENT]

    def main_agent(self) -> Agent:
        agents = self.agents()
        return next((agent for agent in agents if agent.is_default), agents[0])

    def agent(self, agent_id: Optional[str]) -> Agent:
        """L'agent qui répond vraiment : un profil disparu (ou hors multiplex) retombe sur le principal."""
        for agent in self.agents():
            if agent.id == agent_id:
                return agent
        return self.main_agent()

    def conversation_id_for(self, agent: Agent) -> str:
        return MAIN_ID if agent.is_default else AGENT_PREFIX + agent.id

    def list(self) -> List[Conversation]:
        main = self.main_agent()
        result = [Conversation(MAIN_ID, main.name, "main", main.id, MAIN_CHAT_ID)]
        for agent in self.agents():
            if not agent.is_default:
                conversation_id = AGENT_PREFIX + agent.id
                result.append(Conversation(conversation_id, agent.name, "agent", agent.id, conversation_id))
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, agent_id FROM topics ORDER BY created_at, id"
            ).fetchall()
        result.extend(Conversation(r["id"], r["title"], "topic", r["agent_id"], r["id"]) for r in rows)
        return result

    def get(self, conversation_id: object) -> Optional[Conversation]:
        if not isinstance(conversation_id, str):
            return None
        return next((c for c in self.list() if c.id == conversation_id), None)

    def main(self) -> Conversation:
        return self.list()[0]

    def for_chat(self, chat_id: Optional[str]) -> Conversation:
        """La conversation d'un chat d'Hermes ; un chat inconnu (sujet retiré) retombe sur le principal."""
        for conversation in self.list():
            if conversation.chat_id == chat_id:
                return conversation
        return self.main()

    def add_topic(self, title: object, agent_id: object = None) -> Conversation:
        clean = clean_line(title, MAX_TITLE_LENGTH)
        if clean is None:
            raise ConversationError("invalid_request")
        if agent_id is None:
            agent = self.main_agent()
        else:
            agent = next((a for a in self.agents() if a.id == agent_id), None)
            if agent is None:
                raise ConversationError("agent_not_found")
        existing = self.list()
        if any(c.title.casefold() == clean.casefold() for c in existing):
            raise ConversationError("conversation_exists")
        base = slugify(clean) or _TOPIC_FALLBACK
        # « feed- » : les conversations des cartes du fil (feed.THREAD_PREFIX), revue finale M7.
        if base in (MAIN_ID, MAIN_CHAT_ID) or base.startswith((AGENT_PREFIX, "feed-")):
            base = f"{_TOPIC_FALLBACK}-{base}"
        taken = {c.id for c in existing}
        candidate, number = base, 2
        while candidate in taken:
            candidate, number = f"{base}-{number}", number + 1
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO topics (id, title, agent_id, created_at) VALUES (?, ?, ?, ?)",
                    (candidate, clean, agent.id, self._clock()),
                )
        except sqlite3.IntegrityError:
            # Deux créations en même temps (l'app et le terminal) : la seconde perd.
            raise ConversationError("conversation_exists")
        return Conversation(candidate, clean, "topic", agent.id, candidate)

    def remove_topic(self, conversation_id: str) -> None:
        conversation = self.get(conversation_id)
        if conversation is None:
            raise ConversationError("conversation_not_found")
        if conversation.kind != "topic":
            raise ConversationError("conversation_protected")
        with self._lock:
            self._conn.execute("DELETE FROM topics WHERE id = ?", (conversation_id,))

    # Cibles « sheldon:<chat> » des tâches planifiées et de « hermes send ».

    def parse_target(self, target: str) -> Optional[Tuple[str, Optional[str]]]:
        name = (target or "").strip()
        if name in ("", MAIN_ID, MAIN_CHAT_ID):
            return MAIN_CHAT_ID, None
        conversation = self.get(name)
        return (conversation.chat_id, None) if conversation is not None else None

    def is_valid_chat(self, chat_id: str) -> bool:
        return any(c.chat_id == chat_id for c in self.list())
