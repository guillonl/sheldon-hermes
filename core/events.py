"""Ce qui part vers les appareils : le flux en direct (/v1/events) et, s'il est réglé, APNs."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .conversations import Conversation, ConversationCatalog
from .feed import FeedItem
from .files import StoredFile
from .hub import EventHub
from .push import PushService
from .requests import Request
from .store import PushTarget

logger = logging.getLogger(__name__)


class Events:
    def __init__(
        self,
        hub: EventHub,
        push: PushService,
        catalog: ConversationCatalog,
        pending: Callable[[], List[Request]] = lambda: [],
        resolve: Optional[Callable[[str], Optional[Conversation]]] = None,
        important_pending: Callable[[], List[Request]] = lambda: [],
    ) -> None:
        # pending : les demandes en attente (le badge) ; resolve : trouve aussi les
        # conversations de cartes du fil, que le catalogue ne connaît pas ; important_pending :
        # les décisions importantes encore en attente (activité en direct, E4).
        self._hub = hub
        self._push = push
        self._catalog = catalog
        self._pending = pending
        self._resolve = resolve or catalog.get
        self._important_pending = important_pending

    @property
    def push(self) -> PushService:
        return self._push

    def sender(self, conversation_id: str) -> str:
        """Le nom en tête d'une notification : l'agent, le sujet ou la carte de la conversation."""
        conversation = self._resolve(conversation_id)
        return conversation.title if conversation is not None else self._catalog.main().title

    def request_changed(self, request: Request, created: bool) -> None:
        self._hub.publish({"type": "request.upsert", "request": request.to_json()})
        # « answering » n'est qu'une réservation le temps d'une réponse : l'app le montre
        # encore en attente, le badge doit donc le compter aussi.
        # Le badge se compte dans _safe_push : une base qui résiste ne fait pas échouer la
        # clarify ou l'approbation d'Hermes (revue finale, M3).
        self._safe_push(lambda: self._push.request_changed(
            request, self.sender(request.conversation_id), created,
            sum(1 for r in self._pending() if r.status in ("pending", "answering")),
        ))
        # E4 : l'Activité en direct des décisions importantes (live_changed ignore tout seul
        # ce qui n'est pas important, D1). `sender` est un résolveur : une bascule vers une
        # autre décision importante en attente peut appartenir à une tout autre conversation.
        self._safe_push(lambda: self._push.live_changed(
            request, created, self._important_pending(), lambda r: self.sender(r.conversation_id),
        ))

    def live_registered(self, device_id: str) -> None:
        """Après un PUT /v1/devices/current/live-activity (core/api._set_live_activity)."""
        self._safe_push(lambda: self._push.live_registered(
            device_id, self._important_pending(), lambda r: self.sender(r.conversation_id),
        ))

    def reply(self, conversation_id: str, text: str, file_name: Optional[str] = None) -> None:
        # file_name : le nom du fichier joint, quand le tour n'a envoyé que ça (push.py
        # choisit alors un corps de repli plutôt qu'un corps vide).
        self._safe_push(lambda: self._push.reply(conversation_id, self.sender(conversation_id), text, file_name))

    def feed_added(self, item: FeedItem) -> None:
        payload = item.to_json()
        self._hub.publish({"type": "feed.upsert", "item": payload})
        self._safe_push(lambda: self._push.task(payload))

    @staticmethod
    def _safe_push(send: Callable[[], None]) -> None:
        # Une notification ratée (APNs, curl, clé) ne doit jamais faire échouer l'appel
        # d'Hermes qui l'a déclenchée : elle est déjà rangée et publiée sur /v1/events.
        try:
            send()
        except Exception:
            logger.exception("Sheldon: push notification failed")

    def creation_added(self, stored: StoredFile) -> None:
        if stored.listed:
            self._hub.publish({"type": "creation.upsert", "creation": stored.creation_json()})

    def voice_play(self, conversation_id: str, stored: StoredFile) -> None:
        self._hub.publish({"type": "voice.play", "conversationId": conversation_id, "attachment": stored.attachment_json()})

    def conversation_changed(self, conversation: Conversation) -> None:
        self._hub.publish({"type": "conversation.upsert", "conversation": conversation.to_json(None)})

    def conversation_deleted(self, conversation_id: str) -> None:
        self._hub.publish({"type": "conversation.deleted", "conversationId": conversation_id})

    def agent_changed(self, agent: Dict[str, Any]) -> None:
        """agent : la forme de GET /v1/agents (un profil créé, renommé ou changé dans Hermes)."""
        self._hub.publish({"type": "agent.upsert", "agent": agent})

    def agent_deleted(self, agent_id: str) -> None:
        self._hub.publish({"type": "agent.deleted", "agentId": agent_id})

    def call_placed(self, call: Dict[str, Any], voip: List[PushTarget], alerts: List[PushTarget]) -> None:
        self._safe_push(lambda: self._push.call(call, voip, alerts))

    def device_paired(self, device: Dict[str, Any]) -> None:
        """device : la forme de GET /v1/devices (current toujours false : l'appareil n'est pas encore connecté)."""
        self._hub.publish({"type": "device.paired", "device": device})
        self._safe_push(lambda: self._push.device_paired(device["id"], device["name"]))
