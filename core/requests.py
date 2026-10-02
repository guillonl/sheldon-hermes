"""Les demandes : ce qu'Hermes attend de Léo, rangé dans sheldon.db et répondu depuis l'app.

Trois origines, deux formes pour l'app :
- « clarify » (question) : l'outil clarify d'Hermes pose une question, avec ou sans choix,
  et bloque le tour jusqu'à la réponse ou au délai agent.clarify_timeout ;
- « approval » (approbation) : une commande dangereuse attend once, session, always ou
  deny, et Hermes refuse seul au bout de approvals.timeout (300 s) ;
- « proposal » (question) : l'agent propose quelque chose de lui-même par l'outil
  sheldon_propose ; rien n'attend dans Hermes, la réponse lui revient comme un message.
La file d'Hermes vit en mémoire du gateway : une demande qui bloque un tour meurt avec
ce tour ou avec le gateway, et l'extension la passe alors à « expired ».
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .errors import SheldonError
from .sqlite import SqliteStore, ensure_columns
from .text import clean_free_text, clean_line, is_forbidden
from .timeutil import iso_utc

APPROVAL_CHOICES = (
    ("once", "Allow once", "primary"),
    ("session", "Allow for this session", "secondary"),
    ("always", "Always allow", "secondary"),
    ("deny", "Deny", "destructive"),
)
STYLES = ("primary", "secondary", "destructive")
BLOCKING_ORIGINS = ("clarify", "approval")
MAX_TITLE_LENGTH = 200
MAX_BODY_LENGTH = 4000
MAX_CATEGORY_LENGTH = 40
MAX_CHOICE_LENGTH = 30
MAX_PROPOSAL_CHOICES = 3
MAX_ANSWER_LENGTH = 2000
MAX_PROPOSAL_MINUTES = 7 * 24 * 60
# D10 (spec de l'Activité en direct des décisions importantes) : au-delà, une proposition
# marquée importante part comme une proposition ordinaire, toutes conversations confondues.
MAX_IMPORTANT_PER_HOUR = 3
# Borne haute de tout délai (agent.clarify_timeout, approvals.timeout, sheldon_propose) :
# une valeur énorme ou infinie ne doit jamais dépasser cette limite ni faire planter to_json().
MAX_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
# Une réservation plus vieille que ça vient d'un processus mort : expire_due() la libère.
STALE_ANSWERING_SECONDS = 120
# L'outil clarify d'Hermes (agent.clarify_callback) ne borne ni ne nettoie la question ni
# ses libellés (constat Minor, relecture du lot 12-13) : bornés ici, comme un libellé de push
# (push.py:MAX_LABEL_LENGTH), avant d'atteindre l'app.
MAX_CLARIFY_LABEL_LENGTH = 64
# Pourquoi une demande a expiré (champ expiredReason de l'app, null tant qu'elle n'a pas expiré) :
# - « ambiguous » : la réponse à une approbation n'a pas été transmise, faute d'une entrée sûre
#   dans la file d'Hermes (voir RequestService._approval_target) ;
# - « timeout » : son échéance est passée ;
# - « hermes_gone » : Hermes ne l'attend plus (tour fini, gateway redémarré, déjà résolue ailleurs).
EXPIRED_AMBIGUOUS = "ambiguous"
EXPIRED_TIMEOUT = "timeout"
EXPIRED_HERMES_GONE = "hermes_gone"

logger = logging.getLogger(__name__)


class RequestError(SheldonError):
    pass


@dataclass(frozen=True)
class Request:
    id: str
    kind: str
    origin: str
    conversation_id: str
    agent_id: str
    title: str
    body: Optional[str]
    category: Optional[str]
    choices: List[Dict[str, str]]
    allows_text: bool
    status: str
    answer: Optional[Dict[str, Any]]
    hermes_ref: Optional[str]
    session_key: Optional[str]
    chat_id: Optional[str]
    created_at: float
    expires_at: Optional[float]
    closed_at: Optional[float] = None
    claimed_at: Optional[float] = None
    seq: int = 0
    # La commande d'une approbation, telle qu'Hermes l'a envoyée (déjà masquée) : relue au
    # moment de répondre pour la retrouver dans la file d'Hermes.
    command: Optional[str] = None
    expired_reason: Optional[str] = None
    # Spec de l'Activité en direct des décisions importantes, D1 : vrai seulement pour une
    # proposition (origin="proposal") que l'agent a marquée ; jamais pour un clarify ni une
    # approbation, même marqués par erreur (RequestStore.insert l'impose).
    important: bool = False

    @property
    def blocking(self) -> bool:
        return self.origin in BLOCKING_ORIGINS

    def choice(self, choice_id: str) -> Optional[Dict[str, str]]:
        return next((c for c in self.choices if c["id"] == choice_id), None)

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "conversationId": self.conversation_id,
            "agentId": self.agent_id,
            "title": self.title,
            "body": self.body,
            "category": self.category,
            "choices": [dict(c) for c in self.choices],
            "allowsText": self.allows_text,
            # « answering » ne dure que le temps d'une réponse : pour l'app, c'est encore en attente.
            "status": "pending" if self.status == "answering" else self.status,
            # answer reste null pour une demande expirée : le motif a son propre champ (l'app
            # exige answeredAt dans answer, constat Important 2 de la relecture du lot 12-13).
            "answer": dict(self.answer) if self.answer else None,
            "expiredReason": self.expired_reason if self.status == "expired" else None,
            "important": self.important,
            "createdAt": iso_utc(self.created_at),
            "expiresAt": iso_utc(self.expires_at) if self.expires_at is not None else None,
        }


_COLUMNS = (
    "seq, id, kind, origin, conversation_id, agent_id, title, body, category, choices, allows_text, "
    "status, answer, hermes_ref, session_key, chat_id, created_at, expires_at, closed_at, claimed_at, "
    "command, expired_reason, important"
)


def _request(row: Optional[sqlite3.Row]) -> Optional[Request]:
    if row is None:
        return None
    return Request(
        id=row["id"], kind=row["kind"], origin=row["origin"], conversation_id=row["conversation_id"],
        agent_id=row["agent_id"], title=row["title"], body=row["body"], category=row["category"],
        choices=json.loads(row["choices"]), allows_text=bool(row["allows_text"]), status=row["status"],
        answer=json.loads(row["answer"]) if row["answer"] else None, hermes_ref=row["hermes_ref"],
        session_key=row["session_key"], chat_id=row["chat_id"], created_at=row["created_at"],
        expires_at=row["expires_at"], closed_at=row["closed_at"], claimed_at=row["claimed_at"], seq=row["seq"],
        command=row["command"], expired_reason=row["expired_reason"], important=bool(row["important"]),
    )


def _clean_clarify_text(value: Any, max_length: int) -> str:
    """Une question ou un libellé de clarify, le titre d'une approbation, jamais recopiés tels
    quels : une seule ligne
    (les sauts de ligne d'Hermes sont des espaces, pas un refus complet, sans quoi le libellé
    disparaîtrait), sans caractère de contrôle ni de format, borné avec une ellipse plutôt que
    tronqué net."""
    text = " ".join(str(value).split())
    cleaned = "".join(char for char in text if not is_forbidden(char))
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1].rstrip() + "…"


def _code_fence(command: str) -> str:
    """Une clôture Markdown plus longue que toute suite d'accents graves de la commande,
    pour qu'une commande qui contient elle-même un bloc de code ne le fasse jamais déborder."""
    longest = 0
    current = 0
    for char in command:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * max(3, longest + 1)


class RequestStore(SqliteStore):
    SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    category TEXT,
    choices TEXT NOT NULL,
    allows_text INTEGER NOT NULL,
    status TEXT NOT NULL,
    answer TEXT,
    hermes_ref TEXT,
    session_key TEXT,
    chat_id TEXT,
    created_at REAL NOT NULL,
    expires_at REAL,
    closed_at REAL,
    claimed_at REAL,
    command TEXT,
    expired_reason TEXT,
    important INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS requests_status ON requests(status, seq);
"""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        super().__init__(path, clock)
        # Une base de l'étape précédente a déjà sa table requests, sans cette colonne : le
        # CREATE TABLE IF NOT EXISTS de SCHEMA ne la touche pas (E1, activité en direct).
        ensure_columns(self._conn, "requests", {"important": "INTEGER NOT NULL DEFAULT 0"})

    def insert(self, **values: Any) -> Request:
        request_id = f"r-{uuid.uuid4().hex}"
        now = self._clock()
        timeout = values.pop("timeout", None)
        if timeout is not None and timeout > 0:
            # nan et les délais négatifs ou nuls n'entrent jamais ici (nan ne passe jamais
            # `> 0`) ; un délai énorme ou infini reste borné, pour que to_json() et
            # pending() ne rencontrent jamais une date hors de portée.
            bounded_timeout = MAX_TIMEOUT_SECONDS if math.isinf(timeout) else min(timeout, MAX_TIMEOUT_SECONDS)
            expires_at = now + bounded_timeout
        else:
            expires_at = None
        # D1 (défense en profondeur) : seule une proposition peut être importante, même si
        # le champ est passé par erreur à un clarify ou une approbation.
        important = bool(values.get("important", False)) and values["origin"] == "proposal"
        with self._lock:
            self._conn.execute(
                "INSERT INTO requests (id, kind, origin, conversation_id, agent_id, title, body, category, "
                "choices, allows_text, status, hermes_ref, session_key, chat_id, created_at, expires_at, command, "
                "important) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id, values["kind"], values["origin"], values["conversation_id"], values["agent_id"],
                    values["title"], values.get("body"), values.get("category"),
                    json.dumps(values["choices"], ensure_ascii=False), int(values.get("allows_text", False)),
                    values.get("hermes_ref"), values.get("session_key"), values.get("chat_id"), now, expires_at,
                    values.get("command"), int(important),
                ),
            )
        request = self.get(request_id)
        assert request is not None
        return request

    def get(self, request_id: str) -> Optional[Request]:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM requests WHERE id = ?", (request_id,)).fetchone()
        return _request(row)

    def known_refs(self, session_key: str) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT hermes_ref FROM requests WHERE session_key = ? AND hermes_ref IS NOT NULL", (session_key,)
            ).fetchall()
        return [row["hermes_ref"] for row in rows]

    def claim(self, request_id: str) -> bool:
        """Réserve la demande pour une réponse : un seul appareil gagne."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE requests SET status = 'answering', claimed_at = ? WHERE id = ? AND status = 'pending'",
                (self._clock(), request_id),
            )
            return cursor.rowcount == 1

    def release(self, request_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET status = 'pending', claimed_at = NULL WHERE id = ? AND status = 'answering'",
                (request_id,),
            )

    def finish(
        self, request_id: str, status: str, answer: Optional[Dict[str, Any]] = None, expired_reason: Optional[str] = None
    ) -> Request:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET status = ?, answer = ?, expired_reason = ?, closed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'answering')",
                (status, json.dumps(answer, ensure_ascii=False) if answer else None, expired_reason, self._clock(), request_id),
            )
        request = self.get(request_id)
        assert request is not None
        return request

    def pending(self) -> List[Request]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM requests WHERE status IN ('pending', 'answering') ORDER BY seq"
            ).fetchall()
        return [_request(row) for row in rows]

    def waiting_proposals(self, conversation_id: str) -> int:
        """Les propositions de cette conversation (sheldon_propose) qui attendent encore Léo."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM requests WHERE origin = 'proposal' AND conversation_id = ? "
                "AND status IN ('pending', 'answering')",
                (conversation_id,),
            ).fetchone()
        return int(row[0])

    def proposals_since(self, since: float) -> int:
        """Les propositions rangées depuis `since`, toutes conversations confondues."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM requests WHERE origin = 'proposal' AND created_at > ?", (since,)
            ).fetchone()
        return int(row[0])

    def important_since(self, since: float) -> int:
        """Les décisions importantes créées depuis `since`, toutes conversations confondues
        (D10 : le plafond horaire se vérifie sur ce total, pas par conversation)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM requests WHERE important = 1 AND created_at > ?", (since,)
            ).fetchone()
        return int(row[0])

    def important_pending(self) -> List[Request]:
        """Les décisions importantes en attente ou en cours de réponse, la plus récente d'abord
        (spec de l'Activité en direct : une seule Activité à la fois, celle-ci)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM requests WHERE important = 1 AND status IN ('pending', 'answering') "
                "ORDER BY seq DESC"
            ).fetchall()
        return [_request(row) for row in rows]

    def decided(self, limit: int) -> List[Request]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM requests WHERE status IN ('answered', 'expired') "
                "ORDER BY closed_at DESC, seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_request(row) for row in rows]


@dataclass
class Resolvers:
    """Comment une réponse rejoint Hermes. Fourni par l'adaptateur, remplacé dans les tests."""

    # resolve_gateway_clarify(clarify_id, texte) -> vrai si Hermes attendait encore
    clarify: Callable[[str, str], bool]
    # resolve_gateway_approval(session_key, choix, request_id=...) -> nombre de demandes débloquées
    approval: Callable[[str, str, Optional[str]], int]
    # renvoie la réponse d'une proposition à Hermes, comme un message de Léo
    proposal: Callable[[Request, str], Awaitable[None]]
    # list_gateway_approvals(session_key) : la file d'Hermes pour la session, de la plus
    # ancienne à la plus récente, relue au moment de répondre ; None si elle est illisible.
    approval_queue: Callable[[str], Optional[List[Dict[str, Any]]]]
    # le masquage qu'Hermes applique à une commande avant de nous l'envoyer
    # (redact_sensitive_text(force=True), gateway/run.py:685-698)
    redact: Callable[[str], str]
    # après une approbation ou une réponse à clarify : relancer l'indicateur d'écriture du
    # chat, qu'Hermes a mis en pause le temps de la question (gateway/run.py:5891, :16752).
    after_answer: Callable[[Request], None] = field(default=lambda request: None)


def validate_proposal(args: Dict[str, Any]) -> Dict[str, Any]:
    """Vérifie les arguments de l'outil sheldon_propose ; RequestError("invalid_request") sinon."""
    title = clean_line(args.get("title"), MAX_TITLE_LENGTH)
    body = args.get("body")
    category = args.get("category")
    choices = args.get("choices")
    minutes = args.get("expires_in_minutes")
    allow_text = args.get("allow_text", False)
    important = args.get("important", False)
    if title is None or not isinstance(choices, list) or not 1 <= len(choices) <= MAX_PROPOSAL_CHOICES:
        raise RequestError("invalid_request")
    if not isinstance(important, bool):
        raise RequestError("invalid_request")
    if body is not None and (not isinstance(body, str) or len(body) > MAX_BODY_LENGTH):
        raise RequestError("invalid_request")
    if category is not None:
        category = clean_line(category, MAX_CATEGORY_LENGTH)
        if category is None:
            raise RequestError("invalid_request")
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_PROPOSAL_MINUTES):
        raise RequestError("invalid_request")
    if not isinstance(allow_text, bool):
        raise RequestError("invalid_request")
    clean_choices: List[Dict[str, str]] = []
    for index, choice in enumerate(choices):
        # Un choix est un libellé seul ou {"label", "style"} ; le premier est le bouton principal.
        if isinstance(choice, str):
            choice = {"label": choice}
        if not isinstance(choice, dict):
            raise RequestError("invalid_request")
        label = clean_line(choice.get("label"), MAX_CHOICE_LENGTH)
        style = choice.get("style", "primary" if index == 0 else "secondary")
        if label is None or style not in STYLES or any(c["label"] == label for c in clean_choices):
            raise RequestError("invalid_request")
        clean_choices.append({"id": f"c{index}", "label": label, "style": style})
    # Revue finale (M9) : échappements, NUL et sens d'écriture retirés, lignes et tabulations gardées.
    clean_body = clean_free_text(body).strip() if isinstance(body, str) else ""
    return {
        "title": title,
        "body": clean_body or None,
        "category": category,
        "choices": clean_choices,
        "allows_text": allow_text,
        "important": important,
        "timeout": minutes * 60 if minutes is not None else None,
    }


class RequestService:
    def __init__(
        self,
        store: RequestStore,
        resolvers: Resolvers,
        publish: Callable[[Request, bool], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        # publish(demande, créée ?) : chaque changement part vers les appareils (et APNs).
        self._store = store
        self._resolvers = resolvers
        self._publish = publish
        self._clock = clock

    @property
    def store(self) -> RequestStore:
        return self._store

    def get(self, request_id: str) -> Optional[Request]:
        return self._store.get(request_id)

    def pending(self) -> List[Request]:
        return self._store.pending()

    def decided(self, limit: int) -> List[Request]:
        return self._store.decided(limit)

    def question(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        question: str,
        choices: Optional[List[Any]],
        clarify_id: str,
        session_key: str,
        chat_id: str,
        timeout: Optional[float],
    ) -> Request:
        labels = [_clean_clarify_text(choice, MAX_CLARIFY_LABEL_LENGTH) for choice in (choices or [])]
        labels = [label for label in labels if label]
        request = self._store.insert(
            kind="question", origin="clarify", conversation_id=conversation_id, agent_id=agent_id,
            title=_clean_clarify_text(question, MAX_TITLE_LENGTH) or "?", body=None, category=None,
            choices=[{"id": f"c{i}", "label": label, "style": "primary" if i == 0 else "secondary"} for i, label in enumerate(labels)],
            # Le bouton « Autre » d'Hermes : une réponse libre est toujours permise.
            allows_text=True, hermes_ref=clarify_id, session_key=session_key, chat_id=chat_id, timeout=timeout,
        )
        self._publish(request, True)
        return request

    def approval(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        command: str,
        description: str,
        allow_session: bool,
        allow_permanent: bool,
        hermes_request_id: Optional[str],
        session_key: str,
        chat_id: str,
        timeout: Optional[float],
    ) -> Request:
        allowed = {"once", "deny"} | ({"session"} if allow_session else set()) | ({"always"} if allow_permanent else set())
        fence = _code_fence(command)
        # Une seule ligne, bornée : une description sur plusieurs lignes, montrée telle quelle par
        # la carte, la page et la confirmation, pourrait imiter une commande au-dessus de la
        # vraie (revue finale du plan 3). La commande reste entière dans le corps.
        title = _clean_clarify_text(description, MAX_TITLE_LENGTH) or "dangerous command"
        request = self._store.insert(
            kind="approval", origin="approval", conversation_id=conversation_id, agent_id=agent_id,
            title=title, body=f"{fence}\n{command}\n{fence}", category=None,
            choices=[{"id": i, "label": label, "style": style} for i, label, style in APPROVAL_CHOICES if i in allowed],
            allows_text=False, hermes_ref=hermes_request_id, session_key=session_key, chat_id=chat_id, timeout=timeout,
            command=command,
        )
        self._publish(request, True)
        return request

    def announce(self, request_id: str) -> Optional[Request]:
        """Une demande créée dans un autre processus (proposition) : on la publie d'ici."""
        request = self._store.get(request_id)
        if request is not None:
            self._publish(request, True)
        return request

    async def answer(self, request_id: str, *, choice_id: Any = None, text: Any = None) -> Request:
        request = self._store.get(request_id)
        if request is None:
            raise RequestError("request_not_found")
        if request.status == "expired":
            raise RequestError("request_expired")
        if request.status != "pending":
            raise RequestError("request_closed")
        if request.expires_at is not None and request.expires_at <= self._clock():
            self._expire(request, EXPIRED_TIMEOUT)
            raise RequestError("request_expired")
        choice = self._validate(request, choice_id, text)
        if not self._store.claim(request.id):
            raise RequestError("request_closed")
        target: Optional[str] = None
        if request.origin == "approval":
            # Relue juste avant de répondre, sans attente entre la lecture de la file et la
            # résolution (_resolve n'attend rien pour une approbation) : aucune autre réponse ne
            # peut s'intercaler sur cette boucle.
            target = self._approval_target(request)
            if target is None:
                closed = self._store.finish(request.id, "expired", expired_reason=EXPIRED_AMBIGUOUS)
                self._publish(closed, False)
                raise RequestError("request_expired")
        response = choice["label"] if choice else clean_free_text(text.strip())
        try:
            resolved = await self._resolve(request, choice, response, target)
        except Exception as error:
            self._store.release(request.id)
            logger.exception(
                "Sheldon: could not deliver the answer to Hermes (request=%s origin=%s)", request.id, request.origin
            )
            raise RequestError("hermes_unavailable") from error
        except BaseException:
            # Annulation (asyncio.CancelledError), arrêt du processus... : la réservation ne
            # doit jamais rester bloquée en answering, mais l'interruption garde sa forme.
            self._store.release(request.id)
            raise
        if not resolved:
            closed = self._store.finish(request.id, "expired", expired_reason=EXPIRED_HERMES_GONE)
            self._publish(closed, False)
            raise RequestError("request_expired")
        answer = {
            "choiceId": choice["id"] if choice else None,
            "text": None if choice else response,
            "answeredAt": iso_utc(self._clock()),
        }
        closed = self._store.finish(request.id, "answered", answer)
        self._publish(closed, False)
        if request.origin in ("approval", "clarify"):
            try:
                self._resolvers.after_answer(closed)
            except Exception:
                # La réponse est déjà rendue et publiée : un accessoire qui échoue (relancer
                # l'indicateur d'écriture, qu'Hermes a mis en pause le temps de la question)
                # ne doit jamais faire tomber la réponse elle-même.
                logger.exception("Sheldon: after-answer hook failed for request %s", closed.id)
        return closed

    def expire_due(self) -> List[Request]:
        now = self._clock()
        expired = []
        for request in self._store.pending():
            if request.status == "answering":
                # Une réservation trop vieille vient d'un processus mort (le gateway ne
                # revient jamais répondre) : elle est libérée, ou expirée si son échéance
                # est elle-même déjà passée.
                if request.claimed_at is not None and now - request.claimed_at > STALE_ANSWERING_SECONDS:
                    if request.expires_at is not None and request.expires_at <= now:
                        expired.append(self._expire(request, EXPIRED_TIMEOUT))
                    else:
                        self._store.release(request.id)
                continue
            if request.expires_at is not None and request.expires_at <= now:
                expired.append(self._expire(request, EXPIRED_TIMEOUT))
        return expired

    def close_turn(self, conversation_id: str) -> List[Request]:
        """Le tour est fini : ce qui le bloquait n'attend plus rien dans Hermes."""
        return [
            self._expire(r, EXPIRED_HERMES_GONE) for r in self._store.pending()
            if r.status == "pending" and r.blocking and r.conversation_id == conversation_id
        ]

    def expire_blocking(self) -> List[Request]:
        """Au démarrage du gateway : les files d'Hermes, en mémoire, sont vides."""
        return [self._expire(r, EXPIRED_HERMES_GONE) for r in self._store.pending() if r.blocking]

    def _expire(self, request: Request, reason: str) -> Request:
        closed = self._store.finish(request.id, "expired", expired_reason=reason)
        self._publish(closed, False)
        return closed

    def _approval_target(self, request: Request) -> Optional[str]:
        """Le request_id d'Hermes à qui transmettre la réponse à cette approbation, cherché dans
        sa file relue maintenant, ou None s'il n'y en a pas de sûr (constat Important 1, ronde 2
        de la relecture du lot 12-13).

        Une réponse ne part jamais sans request_id : sans lui, Hermes débloque sa plus ancienne
        approbation en attente pour la session (queue.pop(0), tools/approval.py:2664), pas
        forcément celle-ci. Démonstration de la relecture : la file illisible à la création, deux
        cartes sans référence ; Léo refuse rm -rf a, puis autorise git push --force, et Hermes
        exécutait rm -rf a.
        - La carte a un hermes_ref : une entrée de la file doit le porter encore.
        - Sinon : la file doit compter exactement UNE entrée qu'aucune carte ne porte, et sa
          commande, masquée comme Hermes la masque avant de l'envoyer, doit être celle de la carte.
        Tout le reste (file illisible, vide, plusieurs candidates, commande différente, référence
        disparue) : None, et la carte expire en « ambiguous ». Hermes refuse alors la commande
        seul, à son délai (approvals.timeout)."""
        session_key = request.session_key or ""
        try:
            queue = self._resolvers.approval_queue(session_key)
            if queue is None:
                problem = "Hermes approval queue unreadable"
            elif request.hermes_ref is not None:
                if any(str(entry.get("request_id") or "") == request.hermes_ref for entry in queue):
                    return request.hermes_ref
                problem = "its Hermes request is no longer queued"
            else:
                known = set(self._store.known_refs(session_key))
                unbound = [entry for entry in queue if str(entry.get("request_id") or "") not in known]
                if len(unbound) != 1 or not unbound[0].get("request_id"):
                    problem = f"{len(unbound)} unmatched Hermes requests"
                elif self._resolvers.redact(str(unbound[0].get("command", ""))) != request.command:
                    problem = "the only unmatched Hermes request is another command"
                else:
                    return str(unbound[0]["request_id"])
        except Exception:
            logger.warning("Sheldon: could not read the Hermes approval queue for request %s", request.id, exc_info=True)
            problem = "Hermes approval queue unreadable"
        # Jamais la commande dans le journal : seulement de quoi retrouver la demande.
        logger.warning(
            "Sheldon: approval %s not sent to Hermes (%s, session %s), expired as ambiguous",
            request.id, problem, session_key,
        )
        return None

    @staticmethod
    def _validate(request: Request, choice_id: Any, text: Any) -> Optional[Dict[str, str]]:
        if (choice_id is None) == (text is None):
            raise RequestError("invalid_request")
        if choice_id is not None:
            choice = request.choice(choice_id) if isinstance(choice_id, str) else None
            if choice is None:
                raise RequestError("invalid_request")
            return choice
        if not request.allows_text or not isinstance(text, str) or not text.strip() or len(text) > MAX_ANSWER_LENGTH:
            raise RequestError("invalid_request")
        return None

    async def _resolve(
        self, request: Request, choice: Optional[Dict[str, str]], response: str, target: Optional[str]
    ) -> bool:
        if request.origin == "clarify":
            return bool(self._resolvers.clarify(request.hermes_ref or "", response))
        if request.origin == "approval":
            # target vient de _approval_target : jamais None ici (answer() a déjà expiré la carte).
            count = self._resolvers.approval(request.session_key or "", choice["id"] if choice else "deny", target)
            if count is None:
                logger.warning("Sheldon: approval resolver returned None for request=%s, treating as 0", request.id)
                count = 0
            return count > 0
        await self._resolvers.proposal(request, response)
        return True
