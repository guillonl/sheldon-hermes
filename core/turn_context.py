"""Le contexte d'un tour venu de l'app (champ `context` de POST /v1/conversations/{id}/messages).

Une phrase dite à voix haute (mode vocal, appel), une réponse d'Hermes coupée, un bouton de
question, un appel décroché : l'app le dit, l'extension le vérifie et le rend en une ligne (les
faits, puis les règles courtes d'un tour dit), que le crochet pre_llm_call (__init__.py) ajoute à
la copie du message que reçoit le modèle. Hermes la garde avec ce message (colonne api_content de
sa base) et la redonne au modèle aux tours suivants : c'est ce qui tient le cache du prompt.
Jamais dans le prompt système, et Léo ne la voit jamais dans son chat (l'historique lit content).
"""
from __future__ import annotations

import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .text import clean_free_text

SURFACES = ("voiceMode", "call")
MAX_SPOKEN_LENGTH = 500
MAX_QUESTION_LENGTH = 200
MAX_LABEL_LENGTH = 30
MAX_REMEMBERED = 64
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_CALL_ID = re.compile(r"c-[0-9a-f]{32}")
_SURFACE_LINES = {
    "voiceMode": "Spoken aloud in Sheldon's voice mode: your reply is read aloud as you write it.",
    "call": "Spoken aloud during a phone call with you: your reply is read aloud as you write it.",
}
# Les règles de PLATFORM_HINT pour un tour dit, en court : une session déjà ouverte garde le prompt
# système rangé avec elle (agent/conversation_loop.py), sans les règles d'une version plus récente.
_SPOKEN_RULES = (
    "Reply in two or three short sentences, the answer first, without Markdown; before a tool, say in one "
    "short sentence what you are checking; put details in one sheldon block after your sentences."
)


# Décision P6-10 : les mots d'un salut au décroché, sans accents ni casse, comme BargeIn.greetings
# de l'app (« Allô ? », « Oui, allô ? », « Hello? »).
_GREETINGS = frozenset({"allo", "oui", "salut", "bonjour", "hello", "hi", "yes"})
_MAX_GREETING_WORDS = 5


def _folded_words(text: str) -> List[str]:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", folded)


def is_greeting(text: str, agent_name: Optional[str] = None) -> bool:
    """Un salut seul, sans rien d'autre : il ne dit rien à Hermes (décision P6-10). Le nom de
    l'agent qui appelle peut suivre le salut (« Allô Hermes ? ») ; le nom seul, sans mot de salut,
    n'en est pas un : il pourrait porter un contenu."""
    words = _folded_words(text)
    if not (0 < len(words) <= _MAX_GREETING_WORDS) or not any(word in _GREETINGS for word in words):
        return False
    name_words = set(_folded_words(agent_name)) if agent_name else set()
    return all(word in _GREETINGS or word in name_words for word in words)


class ContextError(ValueError):
    """Un `context` mal formé : l'API répond 400 invalid_request, avant de retenir le message."""


@dataclass(frozen=True)
class TurnContext:
    surface: Optional[str] = None
    interrupted: bool = False
    interrupted_message_id: Optional[str] = None
    spoken: Optional[str] = None
    question: Optional[str] = None
    label: Optional[str] = None
    call_id: Optional[str] = None
    device_locked: bool = False


def parse_context(value: Any) -> Optional[TurnContext]:
    """Le `context` de l'app, vérifié : types, bornes, motifs. Absent ou vide : None. Les clés
    inconnues sont ignorées (une app plus récente peut en envoyer d'autres). Les textes perdent
    leurs caractères de contrôle et de format ; un texte trop long est refusé, jamais coupé."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContextError("context")
    surface = value.get("surface")
    if surface is not None and surface not in SURFACES:
        raise ContextError("surface")
    call_id = value.get("callId")
    if call_id is not None and (not isinstance(call_id, str) or not _CALL_ID.fullmatch(call_id)):
        raise ContextError("callId")
    device_locked = value.get("deviceLocked")
    if device_locked is not None and not isinstance(device_locked, bool):
        raise ContextError("deviceLocked")
    interrupted = _object(value, "interrupted")
    message_id = spoken = None
    if interrupted is not None:
        message_id = interrupted.get("messageId")
        if message_id is not None and (not isinstance(message_id, str) or not _MESSAGE_ID.fullmatch(message_id)):
            raise ContextError("interrupted.messageId")
        spoken = _text(interrupted.get("spoken"), MAX_SPOKEN_LENGTH)
    answering = _object(value, "answering")
    question = label = None
    if answering is not None:
        label = _text(answering.get("label"), MAX_LABEL_LENGTH)
        if label is None:
            raise ContextError("answering.label")
        question = _text(answering.get("question"), MAX_QUESTION_LENGTH)
    context = TurnContext(surface, interrupted is not None, message_id, spoken, question, label, call_id, device_locked is True)
    return None if context == TurnContext() else context


def render_context(context: TurnContext, placed_call: Optional[str] = None) -> Optional[str]:
    """La ligne ajoutée au message de Léo pour ce tour, en anglais comme PLATFORM_HINT : les faits,
    puis, pour un tour dit à voix haute, ses règles courtes ; None s'il n'y a rien à dire.
    `placed_call` : la raison de l'appel rangé par l'extension (CallStore, jamais un texte de
    l'app), "" pour un appel sans raison ; None : pas d'appel connu."""
    parts = []
    if context.surface is not None:
        parts.append(_SURFACE_LINES[context.surface])
    if context.device_locked:
        # Au passé et rattaché au message : la ligne reste sous les yeux du modèle aux tours suivants.
        parts.append(
            "The user's iPhone was locked when they said this: it answers none of your pending questions or proposals."
        )
    if context.interrupted:
        parts.append(
            f'The user cut your previous spoken reply after: "{_quoted(context.spoken)}".' if context.spoken
            else "The user cut your previous spoken reply."
        )
    if context.label is not None:
        parts.append(
            f'The user answered "{_quoted(context.label)}" to your question "{_quoted(context.question)}".'
            if context.question else f'The user answered "{_quoted(context.label)}".'
        )
    if placed_call is not None:
        parts.append(
            f'You placed this call (reason you gave: "{_quoted(placed_call)}"); the user just picked up.'
            if placed_call else "You placed this call; the user just picked up."
        )
    if context.surface is not None:
        parts.append(_SPOKEN_RULES)
    return "[Sheldon] " + " ".join(parts) if parts else None


@dataclass(frozen=True)
class AppMessage:
    """Un message de l'app remis à Hermes : sa conversation, son texte, et la ligne de son contexte
    s'il en a un."""
    message_id: str
    conversation_id: str
    text: str
    line: Optional[str] = None


class TurnContexts:
    """Les derniers messages de l'app remis à Hermes, dans le processus du gateway : le runtime
    range chacun juste avant de le remettre à Hermes, le crochet pre_llm_call cherche celui que
    l'agent commence (sur un autre fil). Bornée : les plus anciens partent, comme les messages
    ouverts du pont."""

    def __init__(self, limit: int = MAX_REMEMBERED) -> None:
        self._limit = limit
        self._messages: "OrderedDict[str, AppMessage]" = OrderedDict()
        # Par suite de tours d'Hermes (le message qui l'a ouverte) : le dernier message pris.
        self._taken: "OrderedDict[str, str]" = OrderedDict()
        self._lock = threading.Lock()

    def remember(self, message: AppMessage) -> None:
        with self._lock:
            self._messages[message.message_id] = message
            self._messages.move_to_end(message.message_id)
            _trim(self._messages, self._limit)

    def line_for(self, message_id: str) -> Optional[str]:
        with self._lock:
            message = self._messages.get(message_id) if message_id else None
        return message.line if message is not None else None

    def texts(self, conversation_id: str) -> List[str]:
        """Les textes des derniers messages de cette conversation, le plus récent d'abord, sans les
        commandes (« /... »)."""
        with self._lock:
            messages = list(self._messages.values())
        return [
            message.text for message in reversed(messages)
            if message.conversation_id == conversation_id and not message.text.lstrip().startswith("/")
        ]

    def take(self, first_message_id: str, user_message: Any) -> Optional[AppMessage]:
        """Le message de l'app qu'Hermes commence à traiter, reconnu à son texte.

        Hermes traite dans une même suite de tours le message qui l'ouvre (`first_message_id`,
        la valeur de HERMES_SESSION_MESSAGE_ID pendant toute la suite, gateway/run.py) et ceux
        qui arrivent pendant qu'elle court, qui le coupent ou attendent dans sa file : seul le
        texte (`user_message`) dit lequel commence. C'est le premier message de la même
        conversation, envoyé depuis, pas encore pris et de ce texte ; à défaut, le dernier pris
        s'il a ce texte (le même tour recommencé). None si aucun : jamais un autre message."""
        if not first_message_id or not isinstance(user_message, str):
            return None
        text = user_message.strip()
        with self._lock:
            first = self._messages.get(first_message_id)
            if first is None:
                return None
            ids = list(self._messages)
            chain = [
                self._messages[message_id] for message_id in ids[ids.index(first_message_id):]
                if self._messages[message_id].conversation_id == first.conversation_id
            ]
            taken = self._taken.get(first_message_id)
            start = next((index + 1 for index, message in enumerate(chain) if message.message_id == taken), 0)
            found = next((message for message in chain[start:] if message.text.strip() == text), None)
            if found is None and start and chain[start - 1].text.strip() == text:
                found = chain[start - 1]
            if found is not None:
                self._taken[first_message_id] = found.message_id
                self._taken.move_to_end(first_message_id)
                _trim(self._taken, self._limit)
        return found

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._taken.clear()


# Un seul par processus : l'API, le runtime et le crochet vivent tous dans le gateway. Ailleurs
# (desktop, commandes), la table reste vide et le crochet ne dit rien.
TURN_CONTEXTS = TurnContexts()


def _trim(items: "OrderedDict[str, Any]", limit: int) -> None:
    while len(items) > limit:
        items.popitem(last=False)


def _object(value: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, dict):
        raise ContextError(key)
    return item


def _text(value: Any, max_length: int) -> Optional[str]:
    """Un texte d'une ligne : caractères de contrôle et de format retirés, blancs réduits à une
    espace. Vide : None. Trop long, ou pas une chaîne : refusé."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContextError("text")
    clean = " ".join(clean_free_text(value).split())
    if len(clean) > max_length:
        raise ContextError("length")
    return clean or None


def _quoted(text: Optional[str]) -> str:
    # Les guillemets de la ligne restent sans ambiguïté pour le modèle.
    return (text or "").replace('"', "'")
