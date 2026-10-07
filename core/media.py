"""Les balises de fichiers d'Hermes et les aperçus en texte simple.

Hermes écrit « MEDIA:/chemin/absolu » sur une ligne pour joindre un fichier, et
« [[audio_as_voice]] » ou « [[as_document]] » pour changer la façon de l'envoyer. Il
les retire du flux en direct mais les garde dans l'historique : l'app ne doit jamais
voir un chemin du Mac mini. En production, l'adaptateur passe la fonction d'Hermes
(BasePlatformAdapter.extract_media) ; celle-ci est la version de repli, ligne par ligne.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Tuple

from .text import mask_secrets

DIRECTIVES = ("[[audio_as_voice]]", "[[as_document]]")
_MEDIA_LINE = re.compile(
    r"""^[ \t]*[`"'*_]{0,3}MEDIA:[ \t]*(?P<path>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|[^\s`"'*]+)[`"'*_]{0,3}[ \t]*$""",
    re.MULTILINE,
)
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKERS = re.compile(r"(^|\s)[#>]+\s*|[*_`~|]")


def has_media(text: str) -> bool:
    return "MEDIA:" in text or any(directive in text for directive in DIRECTIVES)


def extract_media(text: str) -> Tuple[List[Tuple[str, bool]], str]:
    """(liste de (chemin, en vocal ?), texte sans les balises), comme Hermes."""
    voice = "[[audio_as_voice]]" in text
    cleaned = text
    for directive in DIRECTIVES:
        cleaned = cleaned.replace(directive, "")
    found: List[Tuple[str, bool]] = []

    def take(match: "re.Match[str]") -> str:
        found.append((os.path.expanduser(match.group("path").strip("`\"'")), voice))
        return ""

    cleaned = _MEDIA_LINE.sub(take, cleaned)
    return found, re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _plain_lines(text: str) -> List[str]:
    """Les lignes lisibles d'un Markdown : sans blocs de code (donc sans blocs visuels),
    sans balises MEDIA, sans marques de mise en forme."""
    lines: List[str] = []
    in_fence = False
    for raw in text.splitlines():
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or _MEDIA_LINE.match(raw):
            continue
        line = raw
        for directive in DIRECTIVES:
            line = line.replace(directive, "")
        line = _LINK.sub(r"\1", line)
        line = _MARKERS.sub(r"\1", line)
        line = re.sub(r"^\s*(?:[-+]|\d+[.)])\s+", "", line)
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return lines


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def plain_preview(text: str, limit: int = 180) -> str:
    """Le texte d'une notification : tout le message en une ligne, borné."""
    return _cap(" ".join(_plain_lines(text)), limit)


def masked_preview(text: str, limit: int = 180) -> str:
    """plain_preview pour l'écran verrouillé (spec 6.5, élargi aux réponses) : masqué avant d'être
    mis en une ligne et borné (coupé à la borne, un numéro ne passerait plus son contrôle et
    garderait ses premiers chiffres), puis encore après, pour un numéro que la mise en une ligne a
    recollé."""
    return mask_secrets(plain_preview(mask_secrets(text), limit))


_BLOCK_FENCES = ("sheldon", "sheldon-block")


def block_summary(text: str) -> str:
    """Le `summary` du premier bloc ```sheldon qui en a un (un objet, ou un tableau d'objets), en
    texte simple et masqué (masked_preview) : le corps d'une notification pour une réponse sans
    phrase (spec 7.2). Un bloc remplacé par un bloc plus récent de même `id` dans ce message (V2,
    `BlockUpdates` côté app) ne parle plus : c'est le plus récent qui le fait. "" sinon, sans jamais
    lever : un JSON illisible ne coûte que le résumé."""
    fence = None
    body: List[str] = []
    items: List[dict] = []
    for raw in text.splitlines():
        if not _FENCE.match(raw):
            if fence is not None:
                body.append(raw)
            continue
        if fence is None:
            language = raw.strip().lstrip("`~").strip().split(" ")[0].lower()
            fence = language
            body = []
            continue
        if fence in _BLOCK_FENCES:
            items.extend(_block_items("\n".join(body)))
        fence = None
    for position, item in enumerate(items):
        block_id = item.get("id")
        if isinstance(block_id, str) and any(later.get("id") == block_id for later in items[position + 1:]):
            continue
        summary = item.get("summary")
        if isinstance(summary, str) and masked_preview(summary):
            return masked_preview(summary)
    return ""


def _block_items(body: str) -> List[dict]:
    """Les blocs d'une clôture dont le résumé se lit : jamais un `link`, dont le `summary` est le
    résumé de sa page, un champ du bloc que l'app ne lit pas comme celui de l'enveloppe
    (BlockAnnotations, readsSummary) ; la notification non plus. Un `link` garde pourtant son `id`
    pour qu'il puisse remplacer un bloc plus ancien, sans résumé."""
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return []
    items = []
    for item in value if isinstance(value, list) else [value]:
        if not isinstance(item, dict):
            continue
        items.append({"id": item.get("id")} if item.get("type") == "link" else item)
    return items


def first_line(text: str, limit: int = 120) -> str:
    """Le résumé d'une carte du fil : la première ligne lisible."""
    lines = _plain_lines(text)
    return _cap(lines[0], limit) if lines else ""
