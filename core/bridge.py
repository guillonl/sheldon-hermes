"""Le relais entre la porte d'entrée et Hermes, sans dépendre d'Hermes.

L'adaptateur Hermes appelle assistant_sent / assistant_edited quand Hermes
écrit ; l'API appelle submit_user_message quand l'app envoie un message.
Chaque événement porte sa conversation (conversationId) : plusieurs tours
peuvent courir en même temps, un par conversation.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from .hub import EventHub
from .timeutil import iso_utc
from .turn_context import TurnContext

logger = logging.getLogger(__name__)

MAX_OPEN_MESSAGES = 256
# Valeur de DEFAULT_STREAMING_CURSOR dans Hermes 0.20.4 et 0.21.4.
DEFAULT_CURSOR = " ▉"
MAIN_CONVERSATION = "main"
INPUT_MODES = ("text", "voice")

# submit(texte, message_id, conversation_id, input_mode, contexte ou None, droit sur le gateway)
Submit = Callable[[str, str, str, str, Optional[TurnContext], bool], Awaitable[None]]


def configured_cursor(gateway_runner: Any, default: str) -> str:
    """Le curseur que le gateway ajoute aux textes en cours (streaming.cursor de sa config)."""
    streaming = getattr(getattr(gateway_runner, "config", None), "streaming", None)
    cursor = getattr(streaming, "cursor", None)
    return cursor if isinstance(cursor, str) else default


@dataclass
class _OpenMessage:
    conversation_id: str
    created_at: str
    text: str = ""
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    # Le message de l'app qui a ouvert le tour où Hermes l'écrit (replyTo), s'il est connu.
    reply_to: Optional[str] = None


class SheldonBridge:
    def __init__(
        self,
        hub: EventHub,
        submit: Submit,
        clock: Callable[[], float] = time.time,
        cursor: str = DEFAULT_CURSOR,
        # (conversation_id, texte, nom du fichier joint s'il n'y a que ça).
        on_reply: Callable[[str, str, Optional[str]], None] = lambda conversation_id, text, file_name: None,
        # (message_id, conversation_id, texte, contexte ou None) : chaque message, rangé pour le
        # crochet pre_llm_call avant de partir à Hermes (son contexte, et quel message Hermes commence).
        on_message: Callable[[str, str, str, Optional[TurnContext]], None] = (
            lambda message_id, conversation_id, text, context: None
        ),
    ) -> None:
        self._hub = hub
        self._submit = submit
        self._clock = clock
        self._cursor = cursor
        self._on_reply = on_reply
        self._on_message = on_message
        self._closed = False
        # Le message de l'app auquel répond le tour en cours, par conversation : celui qui l'a
        # ouvert (turn.started), puis celui qu'Hermes y a pris ensuite (turn_took).
        self._turn_messages: Dict[str, str] = {}
        # Tous les messages de l'app que le tour en cours a pris, par conversation.
        self._turn_taken: Dict[str, Set[str]] = {}
        # Conversations dont un tour est en cours, entre turn_started et turn_finished.
        self._open_turns: Set[str] = set()
        # Dernier texte non vide (ou, sans texte, le nom du fichier joint) d'Hermes pendant
        # le tour en cours, par conversation : c'est lui que montre la notification de fin
        # de tour. None (clé absente) : rien à notifier.
        self._last_reply: Dict[str, Tuple[str, Optional[str]]] = {}
        # Messages d'Hermes pas encore marqués finals, dans l'ordre d'envoi.
        self._open: "OrderedDict[str, _OpenMessage]" = OrderedDict()

    @property
    def open_message_count(self) -> int:
        return len(self._open)

    def is_turn_open(self, conversation_id: str = MAIN_CONVERSATION) -> bool:
        """Un tour est déjà en cours pour cette conversation : sa fin notifiera elle-même
        (_last_reply). Sert à ne jamais notifier deux fois un fichier envoyé hors tour."""
        return conversation_id in self._open_turns

    def close(self) -> None:
        # Posé au début de l'arrêt du gateway : un message accepté maintenant pourrait être
        # annulé avant qu'Hermes l'enregistre, alors que l'app le croirait livré.
        self._closed = True

    async def submit_user_message(
        self,
        text: str,
        client_message_id: str,
        conversation_id: str = MAIN_CONVERSATION,
        input_mode: str = "text",
        context: Optional[TurnContext] = None,
        gateway_control: bool = True,
    ) -> None:
        """gateway_control : False pour un message que l'utilisateur n'a pas écrit tel quel (la réponse à une
        proposition, écrite par Sheldon) ; il ne répond alors à aucune question clarify en attente
        et ne lance aucune commande (revue finale du plan 6, I2)."""
        if self._closed:
            raise RuntimeError("Sheldon is shutting down")
        message_id = f"u-{client_message_id}"
        try:
            self._on_message(message_id, conversation_id, text, context)
        except Exception:
            # Un contexte perdu ne doit pas empêcher le message de partir.
            logger.exception("Sheldon: the turn context could not be kept")
        # D'abord Hermes, ensuite l'écho : un message refusé n'apparaît sur aucun appareil.
        await self._submit(text, message_id, conversation_id, input_mode, context, gateway_control)
        self._publish_message(
            conversation_id,
            {
                "id": message_id,
                "role": "user",
                "text": text,
                "createdAt": iso_utc(self._clock()),
                "clientMessageId": client_message_id,
            },
            final=True,
        )

    def assistant_sent(
        self,
        text: str,
        conversation_id: str = MAIN_CONVERSATION,
        attachments: Optional[List[Dict[str, Any]]] = None,
        reply_to: Optional[str] = None,
    ) -> str:
        message_id = f"a-{uuid.uuid4().hex}"
        # Hors d'un tour de cette conversation (par exemple le message d'erreur qu'Hermes envoie
        # après on_processing_complete, ou une tâche planifiée), aucun turn.finished ne viendra
        # le clore : il part final.
        final = conversation_id not in self._open_turns
        self._remember(message_id, conversation_id, reply_to)
        self._publish_assistant(message_id, text, final=final, attachments=attachments)
        return message_id

    def assistant_edited(
        self, message_id: str, text: str, final: bool, conversation_id: str = MAIN_CONVERSATION
    ) -> None:
        if message_id not in self._open:
            self._remember(message_id, conversation_id)
        self._publish_assistant(message_id, text, final=final)

    def typing(self, conversation_id: str = MAIN_CONVERSATION) -> None:
        self._hub.publish({"type": "agent.typing", "conversationId": conversation_id})

    def turn_started(self, conversation_id: str = MAIN_CONVERSATION, message_id: Optional[str] = None) -> None:
        self._open_turns.add(conversation_id)
        self._last_reply.pop(conversation_id, None)
        event: Dict[str, Any] = {"type": "turn.started", "conversationId": conversation_id}
        if message_id:
            # Le message de l'app qui ouvre ce tour : ses réponses le nommeront (replyTo).
            self._turn_messages[conversation_id] = message_id
            self._turn_taken[conversation_id] = {message_id}
            event["messageId"] = message_id
        else:
            self._turn_messages.pop(conversation_id, None)
            self._turn_taken[conversation_id] = set()
        self._hub.publish(event)

    def turn_took(self, conversation_id: str, message_id: str) -> None:
        """Hermes commence, dans le tour ouvert de cette conversation, un message de l'app arrivé
        pendant ce tour (il l'a coupé, ou il attendait dans sa file) : un nouveau turn.started le
        nomme, et les réponses qui suivent comme la fin du tour y répondent."""
        if conversation_id not in self._open_turns or self._turn_messages.get(conversation_id) == message_id:
            return
        self._turn_messages[conversation_id] = message_id
        self._turn_taken.setdefault(conversation_id, set()).add(message_id)
        self._hub.publish({"type": "turn.started", "conversationId": conversation_id, "messageId": message_id})

    def turn_finished(
        self, outcome: str, conversation_id: str = MAIN_CONVERSATION, message_id: Optional[str] = None
    ) -> None:
        # Le dernier message pris dans ce tour, plutôt que celui qui l'a ouvert (le seul que
        # connaît Hermes à la fin, gateway/platforms/base.py, on_processing_complete).
        turn_message = self._turn_messages.pop(conversation_id, None) or message_id
        self._turn_taken.pop(conversation_id, None)
        # Quand le streaming général est coupé, une réponse part souvent par un
        # seul `send` (jamais suivi d'un edit_message(finalize=True)) : sans ceci, aucun
        # message ne serait jamais marqué final sur ces tours-là. On republie donc, dans
        # l'ordre d'envoi, chaque message encore ouvert de cette conversation avec son
        # dernier texte connu, avant d'annoncer la fin du tour.
        for open_id, entry in list(self._open.items()):
            if entry.conversation_id != conversation_id:
                continue
            self._publish_message(conversation_id, self._message(open_id, entry), final=True)
            del self._open[open_id]
        self._open_turns.discard(conversation_id)
        reply = self._last_reply.pop(conversation_id, None)
        event: Dict[str, Any] = {"type": "turn.finished", "conversationId": conversation_id, "outcome": outcome}
        if turn_message:
            event["messageId"] = turn_message
        self._hub.publish(event)
        # Une notification seulement quand Hermes a vraiment répondu (du texte ou, à défaut,
        # un fichier) : jamais pour un tour vide, raté ou annulé.
        if outcome == "success" and reply is not None:
            text, file_name = reply
            try:
                self._on_reply(conversation_id, text, file_name)
            except Exception:
                # Une notification ratée ne doit pas faire échouer la fin du tour.
                logger.exception("Sheldon: the end-of-turn callback failed")

    def _remember(self, message_id: str, conversation_id: str, anchor: Optional[str] = None) -> None:
        reply_to = None
        if conversation_id in self._open_turns:
            # Le message que nomme Hermes (reply_to de send), s'il est l'un de ceux que ce tour a
            # pris : la fin d'une réponse coupée reste la sienne. Sinon, le message du tour.
            taken = self._turn_taken.get(conversation_id, set())
            reply_to = anchor if anchor in taken else self._turn_messages.get(conversation_id)
        self._open[message_id] = _OpenMessage(conversation_id, iso_utc(self._clock()), reply_to=reply_to)
        while len(self._open) > MAX_OPEN_MESSAGES:
            self._open.popitem(last=False)

    @staticmethod
    def _message(message_id: str, entry: _OpenMessage) -> Dict[str, Any]:
        message: Dict[str, Any] = {
            "id": message_id, "role": "assistant", "text": entry.text, "createdAt": entry.created_at,
        }
        if entry.attachments:
            message["attachments"] = list(entry.attachments)
        if entry.reply_to:
            message["replyTo"] = entry.reply_to
        return message

    def _publish_assistant(
        self, message_id: str, text: str, final: bool, attachments: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        entry = self._open[message_id]
        entry.text = self._strip_cursor(text)
        if attachments is not None:
            entry.attachments = list(attachments)
        self._publish_message(entry.conversation_id, self._message(message_id, entry), final=final)
        if entry.conversation_id in self._open_turns and (entry.text.strip() or entry.attachments):
            # Sans texte, une pièce jointe seule (une image envoyée sans légende) reste une
            # vraie réponse : son nom sert de repli pour la notification de fin de tour.
            file_name = entry.attachments[0].get("name") if entry.attachments else None
            self._last_reply[entry.conversation_id] = (entry.text, file_name)
        if final:
            self._open.pop(message_id, None)

    def _publish_message(self, conversation_id: str, message: Dict[str, Any], final: bool) -> None:
        self._hub.publish(
            {"type": "message.upsert", "conversationId": conversation_id, "message": message, "final": final}
        )

    def _strip_cursor(self, text: str) -> str:
        if self._cursor and text.endswith(self._cursor):
            return text[: -len(self._cursor)]
        return text
