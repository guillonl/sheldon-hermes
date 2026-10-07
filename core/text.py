"""Textes d'une ligne venus de l'app ou de l'agent (noms, titres, libellés de boutons).

Ils s'affichent tels quels dans le terminal, les notifications et l'app : on refuse
les caractères de contrôle (retours à la ligne, échappements ANSI) et de format (sens
d'écriture, espaces invisibles), sauf la liaison des emoji composés.
"""
from __future__ import annotations

import re
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



# Spec 6.5 : le titre et la catégorie d'une demande, et le titre de l'Activité en direct, passent
# par les serveurs d'Apple jusqu'à l'écran verrouillé. Trois motifs y sont masqués, dans cet ordre ;
# chacun garde le texte quand son contrôle échoue. Les espaces comptent avec les insécables, qui
# séparent les chiffres en français.
_SPACES = " \N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}"
_IBAN = re.compile(rf"\b[A-Z]{{2}}\d{{2}}(?:[{_SPACES}]?[A-Z0-9]){{11,30}}\b")
_CARD = re.compile(rf"\b\d(?:[{_SPACES}-]?\d){{12,18}}\b")
_KEYWORD = r"\b(?:code|otp|pin|passcode|mot de passe|password|vérification|verification)\b"
_DIGITS = rf"\d(?:[{_SPACES}-]?\d){{3,7}}"
# Le code à 12 caractères au plus après son mot-clé (« Code : 482913 », spec 6.5), puis à 20 au plus
# avant (« G-482913 is your Google verification code », revue finale 47, I1, décision du lead : le nom
# du service s'y glisse). Jamais le bout d'un plus long numéro : ni chiffre juste après, ni
# chiffre juste avant, séparateur compris, ni les 4 derniers caractères gardés d'un IBAN ou d'une
# carte masqués (« •••• 0189 »). Le second motif garde ce qui précède le code dans son groupe de
# tête plutôt que par un lookbehind, que le Regex de Swift ne connaît pas : l'app reprend les deux
# motifs tels quels (BlockDecoder.maskedSecrets).
_CODE = re.compile(rf"({_KEYWORD}[^\d\n]{{0,12}}?)({_DIGITS})(?![{_SPACES}-]?\d)", re.IGNORECASE)
_CODE_FIRST = re.compile(
    rf"(^[{_SPACES}-]?|[^\d\N{{BULLET}}{_SPACES}-]|[^\d\N{{BULLET}}][{_SPACES}-])({_DIGITS})([^\d\n]{{0,20}}?{_KEYWORD})",
    re.IGNORECASE,
)
_HIDDEN = "\N{BULLET}" * 4 + " "


def _iban_ok(compact: str) -> bool:
    """Le contrôle modulo 97 : les quatre premiers caractères à la fin, chaque lettre en nombre (A = 10)."""
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def _luhn_ok(digits: str) -> bool:
    """La clé de Luhn : en partant de la droite, un chiffre sur deux doublé (moins 9 au-delà de 9)."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _masked_iban(match: "re.Match[str]") -> str:
    text = match.group(0)
    # Le motif peut avaler un mot qui suit (« … 189 EUR ») : on retire les groupes de la fin un à un.
    candidate = text
    while True:
        compact = re.sub(f"[{_SPACES}]", "", candidate)
        if len(compact) >= 15 and _iban_ok(compact):
            return _HIDDEN + compact[-4:] + text[len(candidate):]
        cut = max(candidate.rfind(space) for space in _SPACES)
        if cut <= 0:
            return text
        candidate = candidate[:cut]


def _masked_card(match: "re.Match[str]") -> str:
    digits = "".join(char for char in match.group(0) if char.isdigit())
    return _HIDDEN + digits[-4:] if _luhn_ok(digits) else match.group(0)


def mask_secrets(text: str) -> str:
    """Un IBAN au contrôle modulo 97 juste et une carte à la clé de Luhn juste deviennent
    « •••• » et leurs 4 derniers caractères ; un code de 4 à 8 chiffres à 12 caractères au plus
    après « code », « OTP », « PIN », « passcode », « mot de passe », « password » ou
    « vérification », ou à 20 au plus avant, devient « ••• ». Un téléphone, un montant, une date ou un numéro de commande
    restent tels quels. Le texte gardé dans sheldon.db et montré dans l'app ne passe pas par ici."""
    text = _IBAN.sub(_masked_iban, text)
    text = _CARD.sub(_masked_card, text)
    text = _CODE.sub(lambda match: match.group(1) + "\N{BULLET}" * 3, text)
    return _CODE_FIRST.sub(lambda match: match.group(1) + "\N{BULLET}" * 3 + match.group(3), text)
