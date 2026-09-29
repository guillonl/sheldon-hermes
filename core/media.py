"""Les balises de fichiers d'Hermes et les aperçus en texte simple.

Hermes écrit « MEDIA:/chemin/absolu » sur une ligne pour joindre un fichier, et
« [[audio_as_voice]] » ou « [[as_document]] » pour changer la façon de l'envoyer. Il
les retire du flux en direct mais les garde dans l'historique : l'app ne doit jamais
voir un chemin du Mac mini. En production, l'adaptateur passe la fonction d'Hermes
(BasePlatformAdapter.extract_media) ; celle-ci est la version de repli, ligne par ligne.
"""
from __future__ import annotations

import os
import re
from typing import List, Tuple

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


def first_line(text: str, limit: int = 120) -> str:
    """Le résumé d'une carte du fil : la première ligne lisible."""
    lines = _plain_lines(text)
    return _cap(lines[0], limit) if lines else ""
