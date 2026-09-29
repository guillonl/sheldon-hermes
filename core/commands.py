"""Les commandes slash qu'un message de l'app peut porter jusqu'à Hermes (décision P6-4).

Une liste permise, pas une liste interdite : une liste d'interdits se contourne toujours (une
commande programmée par /loop, une commande shell par /goal gate add). Seules passent les
commandes qui ne lancent aucun code ni commande shell, ne changent aucune garde (approbations,
relecture de la mémoire ou des skills, bac à sable), ne programment aucune autre commande et ne
touchent pas au gateway, avec les arguments dits ici. Toute autre reçoit une phrase qui renvoie
au terminal de l'ordinateur d'Hermes (adapter.py). Le détail, ligne à ligne dans Hermes 0.20.4 :
rapport de la tâche 1 du plan 6, ronde 4. Rien d'Hermes n'est importé ici.
"""
from __future__ import annotations

import unicodedata
from typing import Callable, Dict, List, Optional, Tuple

from .text import clean_free_text

# Ce qui ne se voit pas devant une commande : blancs, caractères de format et de contrôle,
# marques combinantes, et les « lettres » de remplissage vides (Hangul, braille).
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Mn", "Me", "Zs", "Zl", "Zp"})
_FILLERS = frozenset("ᅟᅠ⠀ㅤﾠ")
# Les valeurs qui rallument une relecture (hermes_cli/write_approval_commands.py:193).
ON_VALUES = frozenset({"on", "true", "yes", "1", "enable", "enabled"})


def command_parts(text: str) -> Tuple[Optional[str], str]:
    """Le nom d'une commande « /nom arguments » et ses arguments, lus comme MessageEvent.get_command
    et get_command_args d'Hermes (gateway/platforms/base.py:2395-2419) : « / » seul ou suivi d'un
    blanc, et un chemin de fichier (« /Users/... »), ne sont pas des commandes : (None, "")."""
    stripped = (text or "").lstrip()
    if not stripped.startswith("/"):
        return None, ""
    parts = stripped.split(maxsplit=1)
    name = parts[0][1:].lower().split("@", 1)[0]
    if not name or "/" in name:
        return None, ""
    return name, parts[1].strip() if len(parts) > 1 else ""


def command_text(text: str) -> Optional[str]:
    """Le texte d'une commande tel qu'il faut la lire, ou None pour un texte libre : caractères de
    contrôle et de format retirés (core/text.py), formes de compatibilité ramenées (NFKC : « ／ »
    devient « / »), ce qui ne se voit pas retiré devant. Une commande masquée ainsi est lue comme
    sa commande de base."""
    probe = unicodedata.normalize("NFKC", clean_free_text(text or ""))
    start = 0
    while start < len(probe) and (
        probe[start].isspace() or probe[start] in _FILLERS or unicodedata.category(probe[start]) in _INVISIBLE_CATEGORIES
    ):
        start += 1
    probe = probe[start:]
    return probe if command_parts(probe)[0] is not None else None


def _any(_words: List[str]) -> bool:
    return True


def _voice(words: List[str]) -> bool:
    # Pas « channel », « join », « leave » : les salons vocaux d'une autre messagerie.
    return len(words) <= 1 and all(word in {"on", "enable", "off", "disable", "tts", "status"} for word in words)


def _fast(words: List[str]) -> bool:
    modes = [word for word in words if word != "--global"]
    return len(modes) <= 1 and all(word in {"normal", "fast", "status", "on", "off"} for word in modes)


def _approvals(words: List[str]) -> bool:
    # Le mode lu, ou rétabli (manual) ; jamais smart ni off.
    return words in ([], ["manual"])


def _write_review(words: List[str]) -> bool:
    # L'état et les écritures en attente, ou la relecture rallumée ; jamais approve, reject ni off.
    if words in ([], ["pending"]):
        return True
    return 1 <= len(words) <= 2 and words[0] in ("approval", "mode") and all(word in ON_VALUES for word in words[1:])


# Nom d'Hermes de la commande (alias résolus) : ses arguments permis, en minuscules, mot à mot.
PERMITTED_COMMANDS: Dict[str, Callable[[List[str]], bool]] = {
    "voice": _voice,
    "new": _any,  # l'argument n'est que le titre de la nouvelle session
    "help": _any,
    "status": _any,
    "stop": _any,
    "fast": _fast,
    "approvals": _approvals,
    "memory": _write_review,
    "skills": _write_review,
}


def permitted(name: str, args: str) -> bool:
    """Si la commande `name` (son nom d'Hermes) avec ces arguments peut partir à Hermes."""
    rule = PERMITTED_COMMANDS.get(name)
    return rule is not None and rule(args.lower().split())
