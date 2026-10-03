"""La provenance d'une demande ou d'une carte du fil : d'où vient ce qu'Hermes propose ou a fait.

Retour du 2 octobre : « Ça aurait pu venir d'un message, d'un mail, de plein d'endroits. Je
ne sais pas d'où ça vient, ni quel était le message pour pouvoir y répondre. » Un objet lu avec
tolérance, dans la forme du bloc `source` de l'app (docs/app/BLOCS.md) : ce qui ne se lit pas est
laissé de côté, jamais une raison de refuser la demande. Le texte du message d'origine reste
entier, jusqu'à MAX_SOURCE_BODY : l'app en fait l'aperçu.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .text import clean_free_text, is_forbidden

MAX_SOURCE_BODY = 20000
MAX_SOURCE_LINE = 300
MAX_SOURCE_LABEL = 40
MAX_SOURCE_PEOPLE = 20
MAX_SOURCE_ATTACHMENTS = 10
MAX_SOURCE_URL = 2000
_PERSON_FIELDS = ("name", "address", "email")
_FENCE = re.compile(r"```sheldon[ \t]*\n(.*?)\n```", re.DOTALL)


def clean_line(value: Any, max_length: int) -> Optional[str]:
    """Une ligne lue avec tolérance : sauts de ligne et tabulations en espaces, caractères de
    contrôle ou de format retirés, bornée à max_length ; None si rien n'en reste."""
    if not isinstance(value, str):
        return None
    kept = "".join(" " if char in "\n\t\r" else char for char in value if char in "\n\t\r" or not is_forbidden(char))
    line = " ".join(kept.split())
    return line[:max_length] or None


def _person(value: Any) -> Optional[Any]:
    """Une personne : « Nom <adresse ou identifiant> », une adresse seule, ou {name, address}."""
    if isinstance(value, str):
        return clean_line(value, MAX_SOURCE_LINE)
    if isinstance(value, dict):
        person = {key: clean_line(value.get(key), MAX_SOURCE_LINE) for key in _PERSON_FIELDS if value.get(key) is not None}
        person = {key: text for key, text in person.items() if text}
        return person or None
    return None


def _people(value: Any) -> Optional[Any]:
    if isinstance(value, list):
        people = [person for person in (_person(item) for item in value[:MAX_SOURCE_PEOPLE]) if person]
        return people or None
    return _person(value)


def _attachment(value: Any) -> Optional[Any]:
    if isinstance(value, str):
        return clean_line(value, MAX_SOURCE_LINE)
    if isinstance(value, dict):
        name = clean_line(value.get("name"), MAX_SOURCE_LINE)
        if not name:
            return None
        attachment: Dict[str, str] = {"name": name}
        for key in ("kind", "detail"):
            text = clean_line(value.get(key), MAX_SOURCE_LABEL if key == "kind" else MAX_SOURCE_LINE)
            if text:
                attachment[key] = text
        return attachment
    return None


def _url(value: Any) -> Optional[str]:
    url = clean_line(value, MAX_SOURCE_URL)
    if not url or not re.match(r"(?i)^https?://[^\s]+$", url):
        return None
    return url


def clean_source(value: Any) -> Optional[Dict[str, Any]]:
    """La provenance gardée, ou None : un objet sans rien qui dise d'où ça vient (une personne,
    un objet, un texte ou un lien) ne vaut rien. Les clés inconnues sont ignorées."""
    if not isinstance(value, dict):
        return None
    source: Dict[str, Any] = {}
    for key in ("kind", "app"):
        text = clean_line(value.get(key), MAX_SOURCE_LABEL)
        if text:
            source[key] = text
    for key in ("from", "to", "cc"):
        people = _people(value.get(key) if key != "from" else value.get("from", value.get("sender")))
        if people:
            source[key] = people
    subject = clean_line(value.get("subject", value.get("title")), MAX_SOURCE_LINE)
    if subject:
        source["subject"] = subject
    date = clean_line(value.get("date"), MAX_SOURCE_LABEL)
    if date:
        source["date"] = date
    body = value.get("body", value.get("text"))
    if isinstance(body, str):
        text = clean_free_text(body).strip()[:MAX_SOURCE_BODY]
        if text:
            source["body"] = text
    url = _url(value.get("url", value.get("link")))
    if url:
        source["url"] = url
    attachments = value.get("attachments")
    if isinstance(attachments, list):
        kept = [item for item in (_attachment(a) for a in attachments[:MAX_SOURCE_ATTACHMENTS * 4]) if item][:MAX_SOURCE_ATTACHMENTS]
        if kept:
            source["attachments"] = kept
    if not any(key in source for key in ("from", "subject", "body", "url")):
        return None
    return source


def source_in_text(text: str) -> Optional[Dict[str, Any]]:
    """La provenance d'une réponse livrée au fil : le premier bloc ```sheldon {"type": "source"}
    de son texte (seul, ou dans un tableau de blocs), ou None."""
    for match in _FENCE.finditer(text or ""):
        try:
            parsed = json.loads(match.group(1))
        except ValueError:
            continue
        candidates: List[Any] = parsed if isinstance(parsed, list) else [parsed]
        for block in candidates:
            if isinstance(block, dict) and str(block.get("type", "")).lower() == "source":
                found = clean_source(block)
                if found:
                    return found
    return None
