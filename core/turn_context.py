"""Le contexte d'un tour venu de l'app (champ `context` de POST /v1/conversations/{id}/messages).

Une phrase dite à voix haute (mode vocal, appel), une réponse d'Hermes coupée, un bouton de
question, un appel décroché : l'app le dit, l'extension le vérifie et le rend en une ligne (les
faits, puis les règles courtes d'un tour dit), que le crochet pre_llm_call (__init__.py) ajoute à
la copie du message que reçoit le modèle. Hermes la garde avec ce message (colonne api_content de
sa base) et la redonne au modèle aux tours suivants : c'est ce qui tient le cache du prompt.
Jamais dans le prompt système, et l'utilisateur ne la voit jamais dans son chat (l'historique lit content).
"""
from __future__ import annotations

import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .text import clean_free_text

SURFACES = ("voiceMode", "call")
MAX_SPOKEN_LENGTH = 500
MAX_QUESTION_LENGTH = 200
MAX_LABEL_LENGTH = 30
MAX_REMEMBERED = 64
# Spec 3.2 : les retours de l'app sur les blocs qu'elle n'a pas pu dessiner, et les constats.
UNDRAWN_REASONS = ("unknownType", "missingField", "unreadable")
MAX_UNDRAWN = 5
# La place qui reste pour les constats persistés (notices.py) une fois feedback et undrawn posés ;
# ne plafonne plus leur somme (plan 8, correctifs finaux, I1 : les deux plafonds sont séparés).
MAX_NOTES = 5
MAX_CATALOG = 99
# L'avis d'un toucher (« Plus de ça », « Moins de ça ») sur un rapport, spec 3.2 et A9.
FEEDBACK_VALUES = ("more", "less")
MAX_FEEDBACK = 3
MAX_FEEDBACK_TITLE = 80
# Le plafond garanti de la ligne : feedback et undrawn sont déjà bornés chacun de leur côté (3 et
# 5, alignés sur ConversationStore.swift) ; aucun des deux ne doit jamais être coupé pour l'autre.
MAX_FEEDBACK_AND_UNDRAWN = MAX_FEEDBACK + MAX_UNDRAWN
_BLOCK_TYPE = re.compile(r"[A-Za-z0-9_ -]{1,40}")
_FIELD = re.compile(r"[A-Za-z0-9_]{1,40}")
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_CALL_ID = re.compile(r"c-[0-9a-f]{32}")
# La langue de la voix de l'app (Réglages › Voix) : une étiquette BCP 47 courte, « fr », « en-CA ».
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")
_LANGUAGE_NAMES = {"fr": "French", "en": "English"}
_SURFACE_LINES = {
    "voiceMode": "Spoken aloud in Sheldon's voice mode: your reply is read aloud as you write it.",
    "call": "Spoken aloud during a phone call with you: your reply is read aloud as you write it.",
}
# Les règles d'un tour dit (spec 4.5), dans la note de chaque tour dit : le hint y renvoie, et une
# session déjà ouverte garde le prompt système rangé avec elle (agent/conversation_loop.py). Le
# physique reste ferme (une voix lit la réponse, une commande ne s'approuve jamais à la voix) ; la
# longueur, l'accusé avant un outil et la relance sont des défauts. Demande du 2026-10-02 : une
# conversation vivante, comme un vrai assistant au téléphone, qui dit "OK, je m'en occupe" avant
# un travail long et relance naturellement ("Autre chose ?").
_SPOKEN_RULES = (
    "Spoken aloud: this reply is read by a voice as you write it, so it has no Markdown, lists, tables or "
    "emoji, and what the user should see goes in one sheldon block after your sentences (blocks are shown, "
    "never read). A command waiting on its approval card is never approved by voice: say in one short "
    "sentence that it waits on its card. By default: the answer first, in two or three short sentences; "
    "before a tool or a long step, one short natural sentence saying what you are doing (such as \"OK, I'm "
    "on it\"); a decision the user can answer with a plain \"yes\" goes through clarify or sheldon_propose as "
    "a short spoken question; a short, varied follow-up such as \"Anything else?\" when it fits, never after "
    "every sentence and never after a goodbye."
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
class Undrawn:
    """Un bloc qu'Hermes a écrit et que l'app n'a pas pu dessiner tel quel (spec 3.2)."""
    reason: str
    type: Optional[str] = None
    field: Optional[str] = None
    time: Optional[str] = None


@dataclass(frozen=True)
class Feedback:
    """L'avis d'un toucher sur un rapport d'Hermes (spec 3.2, A9) : « Plus de ça » ou « Moins de ça »."""
    on: str
    value: str
    time: Optional[str] = None


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
    language: Optional[str] = None
    catalog: Optional[int] = None
    undrawn: Tuple[Undrawn, ...] = ()
    feedback: Tuple[Feedback, ...] = ()


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
    language = value.get("language")
    if language is not None and (not isinstance(language, str) or not _LANGUAGE.fullmatch(language)):
        raise ContextError("language")
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
    context = TurnContext(
        surface, interrupted is not None, message_id, spoken, question, label, call_id, device_locked is True, language,
        catalog=_catalog(value.get("catalog")), undrawn=_undrawn(value.get("undrawn")),
        feedback=_feedback(value.get("feedback")),
    )
    return None if context == TurnContext() else context


def _catalog(value: Any) -> Optional[int]:
    """La version du catalogue que dessine l'appareil, de 1 à 99 ; sinon None, jamais un refus :
    ce ne sont pas les mots de l'utilisateur (spec 3.2)."""
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_CATALOG:
        return value
    return None


def _undrawn(value: Any) -> Tuple[Undrawn, ...]:
    """Les blocs que l'app n'a pas pu dessiner, cinq au plus. Une entrée qui ne suit pas ses
    motifs est sautée, jamais refusée : un diagnostic ne coûte jamais un message (spec 3.2)."""
    if not isinstance(value, list):
        return ()
    found: List[Undrawn] = []
    for item in value:
        if len(found) == MAX_UNDRAWN:
            break
        if not isinstance(item, dict):
            continue
        reason, block_type, field, time = (item.get(key) for key in ("reason", "type", "field", "time"))
        if reason not in UNDRAWN_REASONS:
            continue
        if block_type is None:
            if reason != "unreadable":
                continue
        elif not isinstance(block_type, str) or not _BLOCK_TYPE.fullmatch(block_type):
            continue
        if field is not None and (reason != "missingField" or not isinstance(field, str) or not _FIELD.fullmatch(field)):
            continue
        if time is not None and (not isinstance(time, str) or not _CLOCK.fullmatch(time)):
            continue
        found.append(Undrawn(reason, block_type, field, time))
    return tuple(found)


def _feedback(value: Any) -> Tuple[Feedback, ...]:
    """Les avis d'un toucher, trois au plus, mêmes règles qu'`undrawn` : une entrée qui ne suit pas
    ses motifs est sautée, jamais refusée. Le titre perd ses caractères de contrôle, tient sur une
    ligne et fait 80 caractères au plus."""
    if not isinstance(value, list):
        return ()
    found: List[Feedback] = []
    for item in value:
        if len(found) == MAX_FEEDBACK:
            break
        if not isinstance(item, dict):
            continue
        on, mark, time = (item.get(key) for key in ("on", "value", "time"))
        if not isinstance(on, str) or mark not in FEEDBACK_VALUES:
            continue
        title = " ".join(clean_free_text(on).split())
        if not title or len(title) > MAX_FEEDBACK_TITLE:
            continue
        if time is not None and (not isinstance(time, str) or not _CLOCK.fullmatch(time)):
            continue
        found.append(Feedback(title, mark, time))
    return tuple(found)


def feedback_line(item: Feedback) -> str:
    """L'avis de l'utilisateur sur un rapport : un constat, jamais un ordre ; à toi d'en faire une préférence."""
    when = f" of {item.time}" if item.time else ""
    return f'The user marked your report "{_quoted(item.on)}"{when} as "{item.value} of this".'


def undrawn_line(item: Undrawn) -> str:
    """Un bloc que l'app n'a pas pu dessiner tel qu'Hermes l'a écrit : un constat, jamais un ordre."""
    when = f" of {item.time}" if item.time else ""
    if item.reason == "unknownType":
        return f"Sheldon does not know your `{item.type}` block{when}; the user saw its fallback."
    if item.reason == "missingField":
        cause = f"`{item.field}` was missing" if item.field else "a required field was missing or unusable"
        return f"Sheldon could not draw your `{item.type}` block{when} ({cause}); the user saw its fallback."
    named = f"your `{item.type}` block" if item.type else "one of your blocks"
    return f"Sheldon could not read {named}{when} (invalid JSON); the user saw its fallback."


def expired_line(title: str) -> str:
    return f'Your request "{_quoted(title)}" expired without an answer.'


def catalog_line(version: int) -> str:
    return (
        f"One of the user's devices runs an older Sheldon that draws block catalogue version {version}: "
        "newer blocks show as their fallback there."
    )


def render_context(
    context: Optional[TurnContext], placed_call: Optional[str] = None, notes: Sequence[str] = ()
) -> Optional[str]:
    """La ligne ajoutée au message de l'utilisateur pour ce tour, en anglais comme PLATFORM_HINT : les faits,
    puis les constats (`notes`, cinq au plus, spec 3.2), puis, pour un tour dit à voix haute, ses
    règles courtes ; None s'il n'y a rien à dire. `placed_call` : la raison de l'appel rangé par
    l'extension (CallStore, jamais un texte de l'app), "" pour un appel sans raison ; None : pas
    d'appel connu."""
    context = context or TurnContext()
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
            (
                f'You placed this call (reason you gave: "{_quoted(placed_call)}"); the user just picked up. '
                "Open with that reason in one short sentence, then wait for their answer."
            ) if placed_call else (
                "You placed this call; the user just picked up. Open with why you called in one short "
                "sentence, then wait for their answer."
            )
        )
    if context.language is not None:
        # La langue de la voix choisie dans l'app : Sheldon écoute et lit dans cette langue (demande
        # du 2026-10-02 : « quand je choisis français, que ça change en français »).
        name = _LANGUAGE_NAMES.get(context.language.split("-")[0])
        parts.append(
            f"Reply in {name}: Sheldon listens to the user and reads your reply aloud in {name}." if name
            else f'Reply in the language "{context.language}": Sheldon listens to the user and reads your reply aloud in it.'
        )
    parts.extend(list(notes)[:MAX_FEEDBACK_AND_UNDRAWN])
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
