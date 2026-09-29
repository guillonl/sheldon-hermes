"""Textes d'une ligne venus de l'app ou de l'agent (noms, titres, libellés de boutons).

Ils s'affichent tels quels dans le terminal, les notifications et l'app : on refuse
les caractères de contrôle (retours à la ligne, échappements ANSI) et de format (sens
d'écriture, espaces invisibles), sauf la liaison des emoji composés.
"""
from __future__ import annotations

import unicodedata
from typing import Optional

# Liaison des emoji composés (par exemple une personne et un ordinateur) : de catégorie Cf,
# mais sans danger.
ZERO_WIDTH_JOINER = "\N{ZERO WIDTH JOINER}"


def is_forbidden(char: str) -> bool:
    """Contrôle (retours à la ligne, échappements ANSI), format (sens d'écriture, espaces
    invisibles) et séparateurs de ligne ou de paragraphe (U+2028, U+2029), sauf la liaison des
    emoji composés. La seule définition : titres, libellés, raison d'un appel, noms de
    fichiers et d'appareils (pairing.py) passent tous par elle."""
    category = unicodedata.category(char)
    return category in ("Cc", "Zl", "Zp") or (category == "Cf" and char != ZERO_WIDTH_JOINER)


def clean_line(value: object, max_length: int) -> Optional[str]:
    """Le texte sans ses blancs de bord, ou None s'il est vide, trop long ou piégé."""
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean or len(clean) > max_length or any(is_forbidden(char) for char in clean):
        return None
    return clean


def clean_free_text(value: str) -> str:
    """Un texte libre (réponse à une demande, corps d'une proposition) : les caractères bannis
    par is_forbidden sont retirés un par un plutôt que de refuser tout le texte (retours à la
    ligne et tabulations gardés, légitimes dans un texte sur plusieurs lignes)."""
    return "".join(char for char in value if char in "\n\t" or not is_forbidden(char))
