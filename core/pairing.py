"""Appairage : le QR code du terminal, l'échange du code, l'inscription du Mac."""
from __future__ import annotations

import base64
import logging
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
from urllib.parse import urlencode

from .store import Device, DeviceStore
from .text import is_forbidden

logger = logging.getLogger(__name__)

PAIR_CODE_TTL_SECONDS = 600
# Le nom s'affiche dans l'annonce d'un nouvel appareil, le seul signal d'alerte si quelqu'un
# d'autre se relie : court, sur une ligne, sans accents empilés à l'infini.
MAX_DEVICE_NAME_LENGTH = 40
MAX_STACKED_MARKS = 3
MAX_CODE_LENGTH = 128
ALLOWED_PLATFORMS = frozenset({"ios", "macos"})
# Ce plafond est partagé par l'outil sheldon_pair et la route authentifiée /v1/pair/offers
# (PairingService.start(by_tool=True)) ; la commande (terminal) n'a pas de limite.
TOOL_OFFERS_PER_HOUR = 5
# Le nom des QR codes que l'outil écrit dans le cache d'images d'Hermes.
PAIR_IMAGE_PREFIX = "sheldon-pair-"
# Des lettres pour Unicode, mais rien à l'écran : les remplissages du hangul et le braille vide.
_INVISIBLE_LETTERS = frozenset(
    "\N{HANGUL CHOSEONG FILLER}\N{HANGUL JUNGSEONG FILLER}\N{HANGUL FILLER}"
    "\N{HALFWIDTH HANGUL FILLER}\N{BRAILLE PATTERN BLANK}"
)
# Contrôle, format et séparateurs de ligne : la définition partagée de text.py (revue finale, M8).
_is_forbidden = is_forbidden


def _is_visible(char: str) -> bool:
    # Zone privée (le logo d'Apple) et non assigné (un emoji plus récent que ce Python) se
    # voient sur l'iPhone et le Mac.
    category = unicodedata.category(char)
    return (category[0] in "LNPS" or category in ("Co", "Cn")) and char not in _INVISIBLE_LETTERS


def _limit_marks(name: str) -> str:
    """Au plus MAX_STACKED_MARKS marques combinantes de suite (accents empilés) : le reste tombe."""
    kept, stacked = [], 0
    for char in name:
        stacked = stacked + 1 if unicodedata.category(char).startswith("M") else 0
        if stacked <= MAX_STACKED_MARKS:
            kept.append(char)
    return "".join(kept)


class PairingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def validate_device(name: object, platform: object) -> Tuple[str, str]:
    if not isinstance(name, str) or not isinstance(platform, str):
        raise PairingError("invalid_request")
    stripped = name.strip()
    if platform not in ALLOWED_PLATFORMS or any(_is_forbidden(char) for char in stripped):
        raise PairingError("invalid_request")
    # Raccourci plutôt que refusé : l'app envoie jusqu'à 80 caractères.
    clean = _limit_marks(stripped)[:MAX_DEVICE_NAME_LENGTH].rstrip()
    if not any(_is_visible(char) for char in clean):
        raise PairingError("invalid_request")
    return clean, platform


def discard_stale_images(store: DeviceStore) -> None:
    """Efface les QR codes de l'outil dont l'offre ne sert plus : utilisée, expirée ou annulée."""
    try:
        for image in store.take_stale_pair_images():
            path = Path(image)
            # Seulement un QR code de l'outil : une ligne de la base ne fait jamais effacer autre chose.
            if path.name.startswith(PAIR_IMAGE_PREFIX) and path.suffix == ".png":
                path.unlink(missing_ok=True)
    except (OSError, sqlite3.Error):
        # Le ménage ne fait jamais échouer un appairage : Hermes retire de toute façon de son
        # cache les images de plus de 24 heures.
        logger.warning("Sheldon: could not remove an old pairing QR code", exc_info=True)


def build_pair_link(host: str, port: int, code: str, server_id: str) -> str:
    query = urlencode({"v": "1", "host": host, "port": str(port), "code": code, "server": server_id})
    return f"sheldon://pair?{query}"


@dataclass(frozen=True)
class PairingOffer:
    code: str
    link: str
    expires_at: float


@dataclass(frozen=True)
class PairResult:
    server_id: str
    owner_token: str
    device_id: str
    device_token: str
    push_key: str
    device: Device


@dataclass(frozen=True)
class EnrollResult:
    server_id: str
    device_id: str
    device_token: str
    push_key: str
    device: Device


class PairingService:
    def __init__(self, store: DeviceStore, ttl_seconds: float = PAIR_CODE_TTL_SECONDS) -> None:
        self._store = store
        self._ttl = ttl_seconds

    def offer_limit_reached(self) -> bool:
        """Vrai si le plafond partagé (l'outil sheldon_pair et /v1/pair/offers) est déjà atteint,
        sans créer d'offre : à vérifier avant de sonder Tailscale (tâche 18, relecture m2).
        start() reste le contrôle qui compte, atomique, sous le même verrou que l'écriture."""
        return self._store.pairing_offer_limit_reached(TOOL_OFFERS_PER_HOUR)

    def start(self, host: str, port: int, by_tool: bool = False) -> PairingOffer:
        """Une nouvelle offre, qui annule la précédente. by_tool : plafonné à TOOL_OFFERS_PER_HOUR
        par heure, un compteur partagé par l'outil sheldon_pair et la route /v1/pair/offers."""
        code = secrets.token_urlsafe(16)
        expires_at = self._store.add_pairing_code(code, self._ttl, TOOL_OFFERS_PER_HOUR if by_tool else None)
        if expires_at is None:
            raise PairingError("pair_offer_limit")
        discard_stale_images(self._store)
        return PairingOffer(code, build_pair_link(host, port, code, self._store.server_id()), expires_at)

    def redeem(self, code: object, device_name: object, platform: object) -> PairResult:
        # Le corps est vérifié avant le code : une faute de frappe ne grille pas le QR.
        name, clean_platform = validate_device(device_name, platform)
        if not isinstance(code, str) or not code or len(code) > MAX_CODE_LENGTH:
            raise PairingError("pair_code_invalid")
        if not self._store.consume_pairing_code(code):
            raise PairingError("pair_code_invalid")
        # Chaque appairage renouvelle la clé du compte ; les appareils déjà reliés gardent la leur.
        owner_token = generate_secret()
        self._store.set_owner_token(owner_token)
        device_token = generate_secret()
        device = self._store.add_device(name, clean_platform, device_token)
        discard_stale_images(self._store)
        return PairResult(self._store.server_id(), owner_token, device.id, device_token, self._push_key(), device)

    def enroll(self, owner_token: object, device_name: object, platform: object) -> EnrollResult:
        if not isinstance(owner_token, str) or not owner_token or not self._store.owner_token_matches(owner_token):
            raise PairingError("owner_token_invalid")
        name, clean_platform = validate_device(device_name, platform)
        device_token = generate_secret()
        device = self._store.add_device(name, clean_platform, device_token)
        return EnrollResult(self._store.server_id(), device.id, device_token, self._push_key(), device)

    def _push_key(self) -> str:
        # base64url (décision A54) : l'app range pushKey dans son trousseau local et
        # recalcule elle-même l'empreinte HMAC des identifiants opaques.
        return base64.urlsafe_b64encode(self._store.push_key()).decode("ascii")
