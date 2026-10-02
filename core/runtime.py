"""Le cœur assemblé de l'extension : magasins, relais, demandes, fil, fichiers et pushes.

L'adaptateur Hermes crée un Runtime au démarrage du gateway et lui passe chaque appel
d'Hermes (send, send_clarify, send_exec_approval, send_voice...). Tout ce qui touche
Hermes passe par HermesPort : l'adaptateur en fournit la vraie version (HermesLink),
les tests un faux. Trois fonctions servent hors du gateway, dans n'importe quel processus
d'Hermes : propose() (l'outil sheldon_propose), place_call() (l'outil sheldon_call) et
queue_delivery() (envoi sans gateway).
Chaque carte du fil a sa conversation, « feed-<id> » (la page « Voir ») : elle commence par
le texte livré, et chaque message que Léo y écrit part à Hermes avec ce texte en contexte.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager, Dict, List, Optional, Protocol, Sequence, Tuple

from . import media
from .agent_watch import AgentWatch
from .bridge import DEFAULT_CURSOR, SheldonBridge, Submit
from .calls import (
    AGENT_ID, CALL_RING_WINDOW_SECONDS, MAX_REASON_LENGTH, REQUESTED_IGNORED, CallStore, call_result, in_quiet_hours,
)
from .conversations import MAX_TITLE_LENGTH, Agent, Conversation, ConversationCatalog, ConversationError
from .events import Events
from .feed import FeedItem, FeedStore, build_steps, last_reasoning, parse_cron_delivery
from .files import FileError, FileStore, StoredFile
from .history import (
    HistoryPage, HistoryReader, MessageLoader, SessionLocator, latest_cron_session, session_key_for,
    state_db_candidates,
)
from .timeutil import iso_utc
from .hub import EventHub
from .outbox import Outbox, OutboxRelay
from .push import PushService, load_config
from .requests import MAX_IMPORTANT_PER_HOUR, Request, RequestError, RequestService, RequestStore, Resolvers, validate_proposal
from .store import DeviceStore
from .text import clean_line
from .turn_context import TURN_CONTEXTS, AppMessage, TurnContext, render_context

logger = logging.getLogger(__name__)

MAX_THREAD_CONTEXT = 4000
# Hermes 0.20.4 envoie le texte d'un cron par sent(..., job_id=...) puis, à part, chaque
# fichier par send_image_file/send_document, sans job_id (cron/scheduler.py). Un fichier reçu
# dans cette fenêtre après la carte de la même conversation la rejoint ; passé ce délai, il
# est livré comme un fichier ordinaire, avec notification (constat Important 2, relecture du
# lot 12-13).
CRON_FILE_ATTACH_WINDOW_SECONDS = 30.0
# Chaque proposition est une notification avec son : un agent manipulé (un courriel lu par une
# tâche planifiée) ou une tâche qui boucle ne doit pas en noyer Léo (revue de la branche, M1).
MAX_WAITING_PROPOSALS = 10
MAX_PROPOSALS_PER_HOUR = 20
PROPOSE_TOOL = "sheldon_propose"
PROPOSE_SCHEMA: Dict[str, Any] = {
    "name": PROPOSE_TOOL,
    "description": (
        "Ask the user to decide something in the Sheldon app, without waiting: the request "
        "appears in the Requests tab and as a notification with your buttons. Use it for "
        "proactive suggestions (add an event, send a draft, publish an episode). Do not use it "
        "for a question you need answered now in this turn: use clarify instead. The user's "
        "answer comes back later as a new message in the conversation. At most 10 proposals wait "
        "per conversation and 20 are made per hour: beyond that, the tool refuses."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The question, one line, at most 200 characters."},
            "body": {"type": "string", "description": "Context in Markdown, Sheldon blocks allowed, at most 4000 characters."},
            "category": {"type": "string", "description": "Short label such as Calendar or Mail, at most 40 characters."},
            "choices": {
                "type": "array", "minItems": 1, "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Button label, at most 30 characters."},
                        "style": {"type": "string", "enum": ["primary", "secondary", "destructive"]},
                    },
                    "required": ["label"],
                },
            },
            "conversation": {"type": "string", "description": "Sheldon conversation id; defaults to the current one or main."},
            "expires_in_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            "allow_text": {"type": "boolean", "description": "Let the user answer with free text too."},
            "important": {
                "type": "boolean",
                "description": (
                    "True only when the primary choice sends something in the user's name, publishes, pays, "
                    "buys, subscribes or cancels, involves other people, or deletes for good. Shown large on "
                    "the iPhone lock screen with two buttons. At most 3 per hour; beyond, the request is made "
                    "as a normal one."
                ),
            },
        },
        "required": ["title", "choices"],
    },
}

CHATS_TOOL = "sheldon_chats"
CHATS_ACTIONS = ["list", "add", "remove"]
CHATS_SCHEMA: Dict[str, Any] = {
    "name": CHATS_TOOL,
    "description": (
        "List, add or remove the topic chats of the Sheldon app. A topic chat is a separate thread for one "
        "subject, with the same agent: it appears in the app at once, and removing it keeps its history in "
        "Hermes. Load the sheldon:agents skill first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": CHATS_ACTIONS, "description": "Defaults to list."},
            "title": {
                "type": "string",
                "description": f"add: the chat's title, one line, at most {MAX_TITLE_LENGTH} characters, not the title of an existing chat.",
            },
            "agent": {
                "type": "string",
                "description": "add: the id of the specialized agent (Hermes profile) this chat talks to; defaults to the main agent.",
            },
            "conversation": {"type": "string", "description": "remove: the id of the topic chat, as listed."},
        },
    },
}
# Une phrase par refus, pour l'agent : jamais le code brut de ConversationError (cli.py, CHAT_REFUSALS).
CHATS_REFUSALS = {
    "invalid_request": f"invalid title: one line, at most {MAX_TITLE_LENGTH} characters",
    "agent_not_found": "unknown agent: use the id of a specialized agent listed by this tool",
    "conversation_exists": "a chat with this title already exists",
    "conversation_not_found": "unknown Sheldon chat: list the chats to get its id",
    "conversation_protected": "only a topic chat can be removed: the main chat and the agents' chats stay",
}


class HermesPort(Protocol):
    """Ce que l'extension demande à Hermes. Vrai port : adapter.HermesLink."""

    def agents(self) -> List[Agent]: ...

    def multiplex(self) -> bool: ...

    def gateway_state_db(self) -> Path: ...

    def open_session_messages(self, db: Path) -> ContextManager[MessageLoader]: ...

    def extract_media(self, text: str) -> Tuple[List[Tuple[str, bool]], str]: ...

    def redact(self, text: str) -> str: ...

    def job_name(self, job_id: str) -> Optional[str]: ...

    def resolve_clarify(self, clarify_id: str, response: str) -> bool: ...

    def resolve_approval(self, session_key: str, choice: str, request_id: Optional[str]) -> int: ...

    def pending_approvals(self, session_key: str) -> Optional[List[Dict[str, Any]]]: ...

    def resume_typing(self, chat_id: str) -> None: ...

    def image_cache_dir(self) -> Path: ...


@dataclass(frozen=True)
class Route:
    """Où Hermes doit recevoir un message de l'app."""

    chat_id: str
    chat_name: str
    # Profil à poser sur source.profile (mode multiplex, agent autre que le principal).
    profile: Optional[str]
    # Contexte passé à Hermes pour ce tour seulement (event.channel_prompt, jamais enregistré).
    channel_prompt: Optional[str] = None


@dataclass(frozen=True)
class Delivery:
    conversation: Conversation
    text: str
    item: Optional[FeedItem]
    files: List[StoredFile]

    def outbox_payload(self) -> Dict[str, Any]:
        return {
            "conversationId": self.conversation.id,
            "text": self.text,
            "feedItemId": self.item.id if self.item else None,
            "fileIds": [f.id for f in self.files],
        }


class Deliveries:
    """Un message qu'Hermes envoie hors d'un tour : tâche planifiée, « hermes send »."""

    def __init__(self, catalog: ConversationCatalog, feed: FeedStore, files: FileStore, port: HermesPort) -> None:
        self._catalog = catalog
        self._feed = feed
        self._files = files
        self._port = port

    def record(self, chat_id: str, content: str, job_id: Optional[str] = None, media_paths: Sequence[str] = ()) -> Delivery:
        conversation = self._catalog.for_chat(chat_id)
        name, header_job, text = parse_cron_delivery(content)
        extracted: List[Tuple[str, bool]] = []
        if media.has_media(text):
            extracted, text = self._port.extract_media(text)
        stored: List[StoredFile] = []
        # Une tâche livrée pendant que le gateway tourne peut porter ses fichiers dans le
        # texte même (balises MEDIA), une autre process les passe à part (media_paths) :
        # les deux sources rejoignent la carte du fil de la même façon.
        for path in (*media_paths, *(path for path, _voice in extracted)):
            try:
                stored.append(self._files.add(path, conversation.id))
            except FileError as error:
                logger.warning("Sheldon: attachment %s skipped (%s)", Path(path).name, error.code)
        job = job_id or header_job
        item = None
        if job:
            agent = self._catalog.agent(conversation.agent_id)
            try:
                session_id = next(
                    (sid for db in state_db_candidates(agent, self._port.gateway_state_db())
                     for sid in [latest_cron_session(db, job)] if sid),
                    None,
                )
            except Exception:
                # La session du cron n'est qu'un accessoire de la page « Voir » : illisible, la
                # livraison passe quand même, sans ses pas (revue finale, M14).
                logger.warning("Sheldon: session of cron job %s unavailable", job, exc_info=True)
                session_id = None
            item = self._feed.add(
                conversation_id=conversation.id, agent_id=agent.id, title=name or self._port.job_name(job) or job,
                text=text, job_id=job, session_id=session_id, file_ids=[f.id for f in stored],
            )
        return Delivery(conversation, text, item, stored)

    def load(self, payload: Dict[str, Any]) -> Delivery:
        conversation = self._catalog.get(payload.get("conversationId")) or self._catalog.main()
        item = self._feed.get(payload["feedItemId"]) if payload.get("feedItemId") else None
        files = [f for f in (self._files.get(i) for i in payload.get("fileIds", [])) if f is not None]
        return Delivery(conversation, str(payload.get("text", "")), item, files)


def thread_conversation(feed: FeedStore, conversation_id: object) -> Optional[Conversation]:
    """La conversation « feed-<id> » d'une carte du fil, ou None."""
    item = feed.for_thread(conversation_id)
    if item is None:
        return None
    return Conversation(item.thread_id, item.title, "thread", item.agent_id, item.thread_id)


def thread_prompt(item: FeedItem) -> str:
    text = item.text if len(item.text) <= MAX_THREAD_CONTEXT else item.text[:MAX_THREAD_CONTEXT] + "…"
    return (
        f"This Sheldon conversation is about a scheduled task result the user already received "
        f"(task: {item.title}). The result was:\n\n{text}\n\n"
        "Answer about this result; the user may ask you to change or redo something."
    )


class ThreadHistory:
    """L'historique d'une conversation de carte : le texte livré d'abord, puis la suite avec Hermes.

    first est relu à chaque page : un fichier de cron joint à la carte après la première
    lecture y apparaît aussi (revue finale, M12)."""

    def __init__(self, reader: HistoryReader, first: Callable[[], Dict[str, Any]]) -> None:
        self._reader = reader
        self._first = first

    def page(self, before: Optional[int], limit: int) -> HistoryPage:
        page = self._reader.page(before, limit)
        if page.next_before is not None:
            return page
        return HistoryPage([self._first()] + page.messages, None)


def proposal_answer_text(request: Request, response: str) -> str:
    lines = [f"[Sheldon] Réponse à ta proposition « {request.title} » : {response}"]
    if request.body:
        lines += ["", "Rappel de la proposition :", request.body]
    return "\n".join(lines)


class Runtime:
    def __init__(
        self,
        *,
        store: DeviceStore,
        port: HermesPort,
        submit: Submit,
        files_dir: Path,
        push: PushService,
        cursor: str = DEFAULT_CURSOR,
        clock: Callable[[], float] = time.time,
        relay_interval: float = 0.5,
    ) -> None:
        db = store.path
        self.store = store
        self.port = port
        self._clock = clock
        # La dernière carte de cron livrée à chaque conversation (catalogue), avec l'heure :
        # voir file_sent() et CRON_FILE_ATTACH_WINDOW_SECONDS.
        self._recent_cron_cards: Dict[str, Tuple[FeedItem, float]] = {}
        self.hub = EventHub()
        self.catalog = ConversationCatalog(db, port.agents, port.multiplex, clock=clock)
        self.files = FileStore(db, files_dir, clock=clock)
        self.feed = FeedStore(db, clock=clock)
        self.outbox = Outbox(db, clock=clock)
        self.calls = CallStore(db, clock=clock)
        self.events = Events(
            self.hub, push, self.catalog, pending=lambda: self.requests.pending(), resolve=self.conversation,
            important_pending=lambda: self.requests.store.important_pending(),
        )
        self.bridge = SheldonBridge(
            self.hub, submit, clock=clock, cursor=cursor, on_reply=self.events.reply, on_message=self._remember_message,
        )
        self.requests = RequestService(
            RequestStore(db, clock=clock),
            Resolvers(
                clarify=port.resolve_clarify,
                approval=port.resolve_approval,
                proposal=self._answer_proposal,
                approval_queue=port.pending_approvals,
                redact=port.redact,
                after_answer=lambda request: port.resume_typing(request.chat_id or ""),
            ),
            self.events.request_changed,
            clock=clock,
        )
        self.deliveries = Deliveries(self.catalog, self.feed, self.files, port)
        # Les profils créés ou retirés pendant que le gateway tourne (voir core/agent_watch.py).
        self.agent_watch = AgentWatch(self.catalog.agents, self.catalog.conversation_id_for, self.outbox, clock=clock)
        self.relay = OutboxRelay(
            self.outbox,
            {
                "request.created": lambda payload: self.requests.announce(payload["requestId"]),
                "delivery": lambda payload: self.publish_delivery(self.deliveries.load(payload), push_plain=True),
                "conversation.upsert": self._conversation_changed,
                "conversation.deleted": lambda payload: self.events.conversation_deleted(payload["conversationId"]),
                "call.placed": self._call_placed,
                "agent.upsert": lambda payload: self.events.agent_changed(payload["agent"]),
                "agent.deleted": lambda payload: self.events.agent_deleted(payload["agentId"]),
            },
            tick=self._periodic,
            interval=relay_interval,
            clock=clock,
        )
        self._histories: Dict[Tuple[str, str, str], Any] = {}
        self._relay_task: Optional["asyncio.Task[None]"] = None

    # Démarrage et arrêt, avec le gateway.

    async def start(self) -> None:
        # Un second appel (reconnexion du gateway) ne doit jamais perdre le relais déjà lancé.
        if self._relay_task is not None:
            return
        try:
            self.requests.expire_blocking()
        except Exception:
            # Le relais doit démarrer même si l'expiration au démarrage échoue : sans lui,
            # plus aucune proposition ni livraison d'un autre processus n'arriverait jamais.
            logger.exception("Sheldon: could not expire blocking requests at startup")
        self._relay_task = asyncio.ensure_future(self.relay.run())

    async def stop(self) -> None:
        self.bridge.close()
        if self._relay_task is not None:
            self._relay_task.cancel()
            await asyncio.gather(self._relay_task, return_exceptions=True)
            self._relay_task = None
        await self.events.push.aclose()

    def close(self) -> None:
        for store in (self.catalog, self.files, self.feed, self.outbox, self.calls, self.requests.store):
            with contextlib.suppress(Exception):
                store.close()

    def _periodic(self) -> None:
        """Chaque tour du relais : les demandes échues expirent ; toutes les 30 s, les agents."""
        try:
            self.requests.expire_due()
        finally:
            self.agent_watch.check()

    # Ce que lit la porte d'entrée.

    def conversation(self, conversation_id: object) -> Optional[Conversation]:
        """Une conversation du catalogue, ou celle d'une carte du fil."""
        return self.catalog.get(conversation_id) or thread_conversation(self.feed, conversation_id)

    def conversation_for_chat(self, chat_id: Optional[str]) -> Conversation:
        return thread_conversation(self.feed, chat_id) or self.catalog.for_chat(chat_id)

    def history_for(self, conversation: Conversation) -> Any:
        agent = self.catalog.agent(conversation.agent_id)
        key = (conversation.id, agent.id, conversation.chat_id)
        reader = self._histories.get(key)
        if reader is None:
            locator = SessionLocator(
                state_db_candidates(agent, self.port.gateway_state_db()),
                session_key_for(conversation, agent),
                include_legacy=conversation.kind == "main",
            )
            reader = HistoryReader(
                locator.session_ids,
                lambda: self.port.open_session_messages(locator.db),
                messages_sent=self.store.has_client_messages,
                attachment_for=self._attachment_for,
                extract_media=self.port.extract_media,
            )
            item = self.feed.for_thread(conversation.id) if conversation.kind == "thread" else None
            if item is not None:
                item_id = item.id
                reader = ThreadHistory(reader, lambda: self._delivered_message(self.feed.get(item_id) or item))
            self._histories[key] = reader
        return reader

    def _delivered_message(self, item: FeedItem) -> Dict[str, Any]:
        message: Dict[str, Any] = {"id": f"t-{item.id}", "role": "assistant", "text": item.text, "createdAt": iso_utc(item.created_at)}
        attachments = [f.attachment_json() for f in (self.files.get(i) for i in item.file_ids) if f is not None]
        if attachments:
            message["attachments"] = attachments
        return message

    def feed_detail(self, item: FeedItem) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if not item.session_id:
            return [], None
        agent = self.catalog.agent(item.agent_id)
        for db in state_db_candidates(agent, self.port.gateway_state_db()):
            if not db.exists():
                continue
            with self.port.open_session_messages(db) as load_messages:
                rows = load_messages(item.session_id)
            if rows:
                return build_steps(rows, self.port.redact), last_reasoning(rows, self.port.redact)
        return [], None

    def _conversation_changed(self, payload: Dict[str, Any]) -> None:
        # Un sujet ajouté depuis le terminal (« hermes sheldon chats add ») : on l'annonce d'ici.
        conversation = self.catalog.get(payload.get("conversationId"))
        if conversation is not None:
            self.events.conversation_changed(conversation)

    def _call_placed(self, payload: Dict[str, Any]) -> None:
        """Un appel rangé par l'outil sheldon_call (n'importe quel processus) : le gateway sonne.

        Au relais, y compris au rejeu après un redémarrage, les garde-fous sont refaits à l'heure
        du relais pour les seuls appareils du plan : callsAllowed, et les heures calmes pour un
        appel non demandé. Un appel de plus de CALL_RING_WINDOW_SECONDS, ou que plus aucun
        appareil n'accepte, ne sonne jamais : il passe en « missed », sans push ni alerte.
        """
        call = self.calls.get(str(payload.get("callId", "")))
        if call is None or call.status != "placed":
            return
        now = self._clock()
        planned = [str(device_id) for key in ("voip", "alerts") if isinstance(payload.get(key), list) for device_id in payload[key]]
        current = {target.device_id: target for target in self.store.push_targets()}
        ringing = [
            current[device_id] for device_id in planned
            if device_id in current and current[device_id].calls_allowed
            and (call.requested or not in_quiet_hours(current[device_id].quiet_hours, now))
        ]
        if now - call.created_at > CALL_RING_WINDOW_SECONDS or not ringing:
            if self.calls.settle(call.id, "missed"):
                logger.info("Sheldon: call %s not rung (%.0f s old, %d device(s) still accept it)", call.id, now - call.created_at, len(ringing))
            return
        if not self.calls.settle(call.id, "rung"):
            return
        self.events.call_placed(
            call.to_json(), [target for target in ringing if target.voip_token], [target for target in ringing if not target.voip_token],
        )

    def _attachment_for(self, path: str) -> Optional[Dict[str, Any]]:
        stored = self.files.for_source(path)
        return stored.attachment_json() if stored is not None else None

    # L'app vers Hermes.

    def route(self, conversation_id: str) -> Route:
        conversation = self.conversation(conversation_id) or self.catalog.main()
        agent = self.catalog.agent(conversation.agent_id)
        profile = agent.id if self.catalog.multiplex() and not agent.is_default else None
        item = self.feed.for_thread(conversation.id) if conversation.kind == "thread" else None
        return Route(conversation.chat_id, conversation.title, profile, thread_prompt(item) if item else None)

    def command_waits(self, conversation_id: str) -> bool:
        """Une commande de cette conversation attend son accord (sa carte, avec Face ID)."""
        conversation = self.conversation(conversation_id) or self.catalog.main()
        return any(r.kind == "approval" and r.conversation_id == conversation.id for r in self.requests.pending())

    def call_agent_name(self, call_id: str, conversation_id: str) -> Optional[str]:
        """Le nom de l'agent d'un appel de cette conversation, relu dans CallStore (décision P6-10
        étendue : un salut qui le nomme, « Allô Hermes ? »). None pour un appel inconnu, ou d'une
        autre conversation."""
        call = self.calls.get(call_id)
        return call.agent_name if call is not None and call.conversation_id == conversation_id else None

    def _remember_message(
        self, message_id: str, conversation_id: str, text: str, context: Optional[TurnContext]
    ) -> None:
        """Le message, rangé pour le crochet pre_llm_call avec la ligne de son contexte s'il en a
        un. La raison d'un appel décroché est relue dans CallStore (l'appel rangé par sheldon_call
        pour cette conversation), jamais prise dans le message ; un appel inconnu, ou d'une autre
        conversation, est ignoré."""
        line = None
        if context is not None:
            placed_call = None
            if context.call_id is not None:
                call = self.calls.get(context.call_id)
                if call is not None and call.conversation_id == conversation_id:
                    placed_call = call.reason
            line = render_context(context, placed_call)
        TURN_CONTEXTS.remember(AppMessage(message_id, conversation_id, text, line))

    async def _answer_proposal(self, request: Request, response: str) -> None:
        # Sheldon écrit ce texte, pas Léo : il ne répond jamais à une question clarify en attente
        # dans la même conversation (revue finale du plan 6, I2).
        await self.bridge.submit_user_message(
            proposal_answer_text(request, response), f"proposal-{request.id[2:18]}", request.conversation_id,
            gateway_control=False,
        )

    # Hermes vers l'app : un appel par méthode de l'adaptateur.

    def sent(
        self, chat_id: str, content: str, metadata: Optional[Dict[str, Any]] = None, reply_to: Optional[str] = None
    ) -> str:
        job_id = (metadata or {}).get("job_id")
        if job_id:
            return self.publish_delivery(self.deliveries.record(chat_id, content, job_id=str(job_id)), push_plain=False)
        return self.bridge.assistant_sent(content, self.conversation_for_chat(chat_id).id, reply_to=reply_to)

    def edited(self, chat_id: str, message_id: str, content: str, final: bool) -> None:
        self.bridge.assistant_edited(message_id, content, final, self.conversation_for_chat(chat_id).id)

    def typing(self, chat_id: str) -> None:
        self.bridge.typing(self.conversation_for_chat(chat_id).id)

    def turn_started(self, chat_id: str, message_id: Optional[str] = None) -> None:
        self.bridge.turn_started(self.conversation_for_chat(chat_id).id, message_id)

    def turn_finished(self, chat_id: str, outcome: str, message_id: Optional[str] = None) -> None:
        conversation_id = self.conversation_for_chat(chat_id).id
        self.bridge.turn_finished(outcome, conversation_id, message_id)
        self.requests.close_turn(conversation_id)

    def file_sent(self, chat_id: str, path: str, kind: Optional[str], caption: Optional[str]) -> str:
        conversation = self.conversation_for_chat(chat_id)
        recent = self._recent_cron_cards.get(conversation.id)
        if (
            recent is not None
            and not self.bridge.is_turn_open(conversation.id)
            and self._clock() - recent[1] <= CRON_FILE_ATTACH_WINDOW_SECONDS
        ):
            # Le fichier d'un cron, envoyé à part et sans job_id : rejoint la carte que son
            # texte vient de livrer à cette même conversation, plutôt qu'un message à part. Un
            # cron livre toujours hors d'un tour ; pendant un tour ouvert par un message de Léo,
            # le fichier est une réponse du chat, jamais un ajout à la carte (constat Important
            # 3, ronde 2 de la relecture du lot 12-13).
            item = recent[0]
            stored = self.files.add(path, item.conversation_id, kind=kind, caption=caption)
            self.feed.attach_file(item.id, stored.id)
            message_id = self.bridge.assistant_sent(caption or "", item.thread_id, [stored.attachment_json()])
            self.events.creation_added(stored)
            return message_id
        stored = self.files.add(path, conversation.id, kind=kind, caption=caption)
        message_id = self.bridge.assistant_sent(caption or "", conversation.id, [stored.attachment_json()])
        self.events.creation_added(stored)
        if not self.bridge.is_turn_open(conversation.id):
            # Hors d'un tour (cron sans texte à côté, ou un envoi direct hors tour) : sans ceci,
            # ce fichier ne serait jamais notifié. Pendant un tour, sa fin s'en charge déjà
            # (bridge.py, _last_reply) : ne pas notifier une seconde fois.
            self.events.reply(conversation.id, caption or "", stored.name)
        return message_id

    def voice_reply(self, chat_id: str, path: str) -> None:
        """Réponse vocale automatique (play_tts) : jouée par l'app, sans bulle ni création."""
        conversation = self.conversation_for_chat(chat_id)
        stored = self.files.add(path, conversation.id, kind="audio", listed=False)
        self.events.voice_play(conversation.id, stored)

    def clarify(
        self,
        *,
        chat_id: str,
        question: str,
        choices: Optional[List[Any]],
        clarify_id: str,
        session_key: str,
        timeout: Optional[float],
    ) -> Request:
        conversation = self.conversation_for_chat(chat_id)
        return self.requests.question(
            conversation_id=conversation.id, agent_id=self.catalog.agent(conversation.agent_id).id,
            question=question, choices=choices, clarify_id=clarify_id, session_key=session_key,
            chat_id=chat_id, timeout=timeout,
        )

    def approval(
        self,
        *,
        chat_id: str,
        command: str,
        description: str,
        session_key: str,
        allow_session: bool,
        allow_permanent: bool,
        timeout: Optional[float],
    ) -> Request:
        # Hermes ne passe pas le request_id : on le retrouve dans sa file d'attente
        # (list_gateway_approvals : command, description, request_id) en appariant par
        # CONTENU, jamais par position. Deux approbations d'une même session mises en file en
        # même temps (des sous-agents en parallèle, tools/delegate_tool.py) arrivent chacune
        # avec le contenu de LEUR commande : une correspondance par position (« la plus
        # récente inconnue ») les croiserait, un refus explicite exécutant alors l'autre
        # commande (constat Critique 1, relecture du lot 12-13). Hermes masque la commande
        # avant de nous l'envoyer (redact_sensitive_text(force=True), gateway/run.py:685-698) :
        # on applique le même masquage à chaque entrée de la file avant de comparer. Si
        # plusieurs entrées correspondent (commandes identiques), la plus ancienne encore
        # inconnue gagne (list_gateway_approvals rend sa file de la plus ancienne à la plus
        # récente). Si aucune ne correspond, ou si la file est illisible, hermes_request_id
        # part à None ; au moment de répondre, RequestService._approval_target relit la file
        # et ne transmet rien sans une entrée sûre.
        known = set(self.requests.store.known_refs(session_key))
        matching = [
            entry for entry in self.port.pending_approvals(session_key) or []
            if entry.get("request_id") and str(entry["request_id"]) not in known
            and self.port.redact(str(entry.get("command", ""))) == command
            and str(entry.get("description", "")) == description
        ]
        hermes_request_id = str(matching[0]["request_id"]) if matching else None
        conversation = self.conversation_for_chat(chat_id)
        return self.requests.approval(
            conversation_id=conversation.id, agent_id=self.catalog.agent(conversation.agent_id).id,
            command=command, description=description, allow_session=allow_session,
            allow_permanent=allow_permanent, hermes_request_id=hermes_request_id,
            session_key=session_key, chat_id=chat_id, timeout=timeout,
        )

    def publish_delivery(self, delivery: Delivery, push_plain: bool) -> str:
        attachments = [f.attachment_json() for f in delivery.files]
        # Une tâche planifiée s'ouvre dans la conversation de sa carte ; un envoi ordinaire, dans sa cible.
        target = delivery.item.thread_id if delivery.item is not None else delivery.conversation.id
        message_id = self.bridge.assistant_sent(delivery.text, target, attachments or None)
        for stored in delivery.files:
            self.events.creation_added(stored)
        if delivery.item is not None:
            self.events.feed_added(delivery.item)
            self._recent_cron_cards[delivery.conversation.id] = (delivery.item, self._clock())
        elif push_plain and (delivery.text.strip() or delivery.files):
            # Un fichier seul (sans texte, venu d'un autre processus) ne doit jamais être
            # notifié avec un corps vide : même repli que Events.reply pour un fichier
            # envoyé pendant un tour (bridge.py, _last_reply).
            no_text = not delivery.text.strip()
            file_name = delivery.files[0].name if no_text and delivery.files else None
            self.events.reply(delivery.conversation.id, delivery.text, file_name)
        return message_id


# Hors du gateway : ces deux fonctions tournent dans n'importe quel processus d'Hermes.


# Liste blanche : les messageries d'Hermes 0.20.4 où un humain écrit lui-même (noms exacts de
# gateway/config.py, Platform, et de plugins/platforms/*/plugin.yaml), plus les extensions de
# Léo, « sheldon » et « fetch ». bluebubbles et photon sont les deux passerelles iMessage.
# Toute autre surface ouvre des tours sans message de Léo : homeassistant (un capteur), a2a (un
# autre agent), ntfy (qui publie sur le sujet), webhook, api_server, relay, email (expéditeur
# falsifiable), une plateforme inconnue ou future ; et le desktop, le TUI et le CLI, qui n'ont
# pas de plateforme. Là, requested n'est pas cru et sheldon_pair refuse. Une messagerie ajoutée
# un jour se déclare ici, jamais par défaut.
CHAT_PLATFORMS = frozenset({
    "telegram", "signal", "whatsapp", "whatsapp_cloud", "discord", "slack", "matrix",
    "bluebubbles", "photon", "sheldon", "fetch",
})
_NOT_CRON = ("", "0", "false", "no", "off")


@dataclass(frozen=True)
class ToolContext:
    """La session qui appelle l'outil, lue par l'adaptateur (gateway.session_context)."""

    platform: str = ""
    chat_id: str = ""
    profile: str = ""
    cron_platform: str = ""
    cron_chat_id: str = ""
    # « 1 » dans une tâche planifiée (HERMES_CRON_SESSION, posé par cron/scheduler.py).
    cron_session: str = ""

    def opened_by_a_message(self) -> bool:
        """Vrai dans un tour de discussion ouvert par un message de Léo (ou relancé dans ce même
        chat) sur une messagerie de CHAT_PLATFORMS ; faux partout ailleurs, et en tâche planifiée."""
        cron = self.cron_session.strip().lower() not in _NOT_CRON or bool(self.cron_platform)
        return not cron and self.platform in CHAT_PLATFORMS and bool(self.chat_id)


def session_conversation(context: ToolContext, catalog: ConversationCatalog, feed: FeedStore) -> Conversation:
    """La conversation Sheldon de la session qui appelle un outil ; hors de Sheldon, le chat principal."""
    if context.platform == "sheldon":
        return thread_conversation(feed, context.chat_id) or catalog.for_chat(context.chat_id)
    if context.cron_platform == "sheldon":
        return catalog.for_chat(context.cron_chat_id or None)
    return catalog.main()


def propose(args: Any, context: ToolContext, db_path: Path, port: HermesPort, clock: Callable[[], float] = time.time) -> str:
    """L'outil sheldon_propose : range la demande et prévient le gateway par la table outbox."""
    if not isinstance(args, dict):
        return json.dumps({"ok": False, "error": "arguments must be an object"})
    catalog = ConversationCatalog(db_path, port.agents, port.multiplex, clock=clock)
    feed = FeedStore(db_path, clock=clock)
    store = RequestStore(db_path, clock=clock)
    outbox = Outbox(db_path, clock=clock)
    try:
        target = args.get("conversation")
        if target is not None:
            conversation = catalog.get(target) or thread_conversation(feed, target)
            if conversation is None:
                return json.dumps({"ok": False, "error": f"unknown Sheldon conversation {target!r}"})
        else:
            conversation = session_conversation(context, catalog, feed)
        try:
            values = validate_proposal(args)
        except RequestError:
            return json.dumps({
                "ok": False,
                "error": "invalid arguments: title (one line, max 200), 1 to 3 choices with distinct labels (max 30), "
                "optional body (max 4000), category (max 40), expires_in_minutes (1 to 10080), allow_text (boolean), "
                "important (boolean)",
            })
        agent = catalog.agent(context.profile or conversation.agent_id)
        if target is None and conversation.agent_id != agent.id:
            # Un profil hors de Sheldon (une messagerie, en multiplex) : la réponse revient dans son
            # propre chat, comme pour un appel (revue finale, M4).
            conversation = catalog.get(catalog.conversation_id_for(agent)) or conversation
        if store.waiting_proposals(conversation.id) >= MAX_WAITING_PROPOSALS:
            return json.dumps({
                "ok": False,
                "error": f"Not proposed: {MAX_WAITING_PROPOSALS} proposals already wait for the user in this conversation. "
                "Wait for their answers, or ask in your normal reply instead.",
            })
        if store.proposals_since(clock() - 3600) >= MAX_PROPOSALS_PER_HOUR:
            return json.dumps({
                "ok": False,
                "error": f"Not proposed: you already made {MAX_PROPOSALS_PER_HOUR} proposals within the last hour. "
                "Ask in your normal reply instead.",
            })
        note = "The user will answer in the Sheldon app; the answer arrives later as a new message in this conversation."
        # D10 (spec de l'Activité en direct des décisions importantes) : au-delà du plafond
        # horaire, toutes conversations confondues, la demande part comme une proposition
        # ordinaire plutôt que d'être refusée ; l'agent en est informé dans sa note.
        if values.get("important") and store.important_since(clock() - 3600) >= MAX_IMPORTANT_PER_HOUR:
            values["important"] = False
            note = (
                f"Not shown as important: {MAX_IMPORTANT_PER_HOUR} important decisions already made within the "
                f"last hour. {note}"
            )
        timeout = values.pop("timeout")
        request = store.insert(
            kind="question", origin="proposal", conversation_id=conversation.id, agent_id=agent.id,
            chat_id=conversation.chat_id, timeout=timeout, **values,
        )
        outbox.append("request.created", {"requestId": request.id})
        return json.dumps({
            "ok": True, "requestId": request.id, "status": "pending", "important": request.important, "note": note,
        })
    finally:
        for closable in (catalog, feed, store, outbox):
            closable.close()


def manage_chats(args: Any, db_path: Path, port: HermesPort, clock: Callable[[], float] = time.time) -> str:
    """L'outil sheldon_chats : les chats de sujet, comme « hermes sheldon chats » (cli.py, run_chats),
    que la garde bloque dans les commandes d'Hermes ; annoncés au gateway par la table outbox."""
    if not isinstance(args, dict):
        return json.dumps({"ok": False, "error": "arguments must be an object"})
    action = args.get("action") or "list"
    if action not in CHATS_ACTIONS:
        return json.dumps({"ok": False, "error": f"unknown action {action!r}: list, add or remove"})
    catalog = ConversationCatalog(db_path, port.agents, port.multiplex, clock=clock)
    outbox = Outbox(db_path, clock=clock)
    try:
        if action == "add":
            conversation = catalog.add_topic(args.get("title"), args.get("agent"))
            outbox.append("conversation.upsert", {"conversationId": conversation.id})
            return json.dumps({
                "ok": True, "chat": _chat_json(conversation),
                "note": f"The chat is in Sheldon now. Scheduled tasks can report to it with --deliver sheldon:{conversation.id}.",
            }, ensure_ascii=False)
        if action == "remove":
            conversation_id = args.get("conversation")
            catalog.remove_topic(conversation_id if isinstance(conversation_id, str) else "")
            outbox.append("conversation.deleted", {"conversationId": conversation_id})
            return json.dumps({"ok": True, "removed": conversation_id, "note": "Its history stays in Hermes."})
        return json.dumps({"ok": True, "chats": [_chat_json(conversation) for conversation in catalog.list()]}, ensure_ascii=False)
    except ConversationError as error:
        return json.dumps({"ok": False, "error": CHATS_REFUSALS.get(error.code, error.code)})
    finally:
        catalog.close()
        outbox.close()


def _chat_json(conversation: Conversation) -> Dict[str, str]:
    return {"id": conversation.id, "title": conversation.title, "kind": conversation.kind, "agent": conversation.agent_id}


def place_call(
    args: Any, context: ToolContext, db_path: Path, apns_dir: Path, port: HermesPort, clock: Callable[[], float] = time.time
) -> str:
    """L'outil sheldon_call : les garde-fous, le registre, puis le gateway fait sonner (table outbox).

    Sécurité (au-delà du brief d'origine) : `requested` n'est qu'un mot donné par l'agent, jamais
    vérifié ailleurs. Un agent manipulé qui le déclarerait toujours vrai contournerait sinon la
    limite horaire des appels non demandés et les heures calmes : les appels demandés sont donc
    bornés eux aussi (plan_call, requested_count), et requested n'est cru que dans un tour ouvert
    par un message de Léo (ronde de sécurité, point 5). Chaque tentative évaluée par les
    garde-fous (appel placé ou refusé, jamais un argument invalide ou un agent inconnu) est
    journalisée avec son identifiant, l'agent et le drapeau requested, jamais la raison.
    """
    if not isinstance(args, dict):
        return call_result("invalid")
    raw_reason = args.get("reason")
    if isinstance(raw_reason, str):
        # Un demi-caractère UTF-16 isolé ne s'écrit ni dans sheldon.db ni vers Apple.
        raw_reason = "".join(char for char in raw_reason if unicodedata.category(char) != "Cs")
    reason = clean_line(raw_reason, MAX_REASON_LENGTH)
    requested = args.get("requested")
    agent_id = args.get("agentId")
    if reason is None or not isinstance(requested, bool):
        return call_result("invalid")
    if agent_id is not None and not (isinstance(agent_id, str) and AGENT_ID.fullmatch(agent_id)):
        return call_result("invalid")
    believed = requested and context.opened_by_a_message()
    note = REQUESTED_IGNORED if requested and not believed else None
    catalog = ConversationCatalog(db_path, port.agents, port.multiplex, clock=clock)
    feed = FeedStore(db_path, clock=clock)
    devices = DeviceStore(db_path, clock=clock)
    calls = CallStore(db_path, clock=clock)
    try:
        if agent_id is not None:
            agent = next((known for known in catalog.agents() if known.id == agent_id), None)
            if agent is None:
                return call_result("unknown_agent", agent=agent_id)
            conversation = catalog.get(catalog.conversation_id_for(agent)) or catalog.main()
        else:
            conversation = session_conversation(context, catalog, feed)
            agent = catalog.agent(context.profile or conversation.agent_id)
            if conversation.agent_id != agent.id:
                # Hors de Sheldon (une messagerie, le desktop), l'agent rappelle depuis son propre chat.
                conversation = catalog.get(catalog.conversation_id_for(agent)) or conversation
        if load_config(apns_dir) is None:
            return call_result("push_off")
        try:
            plan, placed = calls.place(
                devices.push_targets(), requested=believed, agent_id=agent.id, agent_name=agent.name,
                conversation_id=conversation.id, reason=reason,
            )
        except sqlite3.Error:
            logger.warning("Sheldon: call not placed (agent=%s requested=%s status=unavailable)", agent.id, believed, exc_info=True)
            return call_result("unavailable", note=note)
        if placed is None:
            logger.warning("Sheldon: call not placed (agent=%s requested=%s status=%s)", agent.id, believed, plan.status)
            return call_result(plan.status, note=note)
        logger.info(
            "Sheldon: call %s placed (agent=%s requested=%s voip=%d alerts=%d)",
            placed.id, agent.id, believed, len(plan.voip), len(plan.alerts),
        )
        return call_result("calling", placed.id, note=note, ringing=len(plan.voip), alerted=len(plan.alerts))
    finally:
        for closable in (catalog, feed, devices, calls):
            closable.close()


def media_paths(media_files: Any) -> List[str]:
    """Hermes passe ses fichiers en (chemin, en vocal ?) ; on ne garde que les chemins."""
    paths: List[str] = []
    for entry in media_files or []:
        path = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        if isinstance(path, str) and path:
            paths.append(path)
    return paths


def queue_delivery(
    chat_id: str,
    message: str,
    media_files: Any,
    db_path: Path,
    files_dir: Path,
    port: HermesPort,
    clock: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """standalone_sender_fn : un envoi vers Sheldon quand le gateway n'est pas dans ce processus."""
    catalog = ConversationCatalog(db_path, port.agents, port.multiplex, clock=clock)
    feed = FeedStore(db_path, clock=clock)
    files = FileStore(db_path, files_dir, clock=clock)
    outbox = Outbox(db_path, clock=clock)
    try:
        if not catalog.is_valid_chat(chat_id):
            return {"error": f"unknown Sheldon chat {chat_id!r}"}
        delivery = Deliveries(catalog, feed, files, port).record(chat_id, message, media_paths=media_paths(media_files))
        entry_id = outbox.append("delivery", delivery.outbox_payload())
        return {"success": True, "message_id": f"outbox-{entry_id}"}
    finally:
        for closable in (catalog, feed, files, outbox):
            closable.close()
