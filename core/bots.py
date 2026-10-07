"""Les bots de Bot Mode (Hermes 0.21), vus par Sheldon : un bot est un profil, donc un agent (spec 12).

Le visage d'un bot est un fichier du dossier de son profil, `assets/avatar.<png|jpg|webp>`, que le
desktop d'Hermes écrit par `profiles.set_asset` (tui_gateway/methods_profiles.py) : une image
choisie, ou le visage dessiné, converti en PNG. Sheldon le lit seulement, ne l'écrit jamais : la
seule source de vérité est ce que l'utilisateur règle dans Hermes.

Le chat d'un bot dans Sheldon peut suivre sa session canonique « Bot Chat », la seule où Hermes
reçoit les messages des autres bots (tools/bot_mode_probe.py, tools/bot_mode_dm.py) : BotChatLinks
garde le lien de chaque chat, plan_link dit quand le faire, peer_author lit l'auteur d'un message
d'un autre bot. Sans Hermes : testé par pytest.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Dict, Optional, Tuple

from .sqlite import SqliteStore

# Les mêmes que Hermes : _ASSET_EXTS (dans cet ordre, celui de profiles.get_asset) et la taille
# refusée par profiles.set_asset (« len(blob) > 2_000_000 »).
AVATAR_NAMES = ("avatar.png", "avatar.jpg", "avatar.webp")
MAX_AVATAR_BYTES = 2_000_000

_SIGNATURES = (
    ("image/png", ((0, b"\x89PNG\r\n\x1a\n"),)),
    ("image/jpeg", ((0, b"\xff\xd8\xff"),)),
    ("image/webp", ((0, b"RIFF"), (8, b"WEBP"))),
)


def image_type(head: bytes) -> Optional[str]:
    """Le type d'une image lu à ses premiers octets, jamais à son nom ; None pour tout autre fichier."""
    for kind, marks in _SIGNATURES:
        if all(head[start:start + len(mark)] == mark for start, mark in marks):
            return kind
    return None


def _regular(path: Path) -> Optional[os.stat_result]:
    """La fiche d'un fichier ordinaire, sans suivre de lien ; None sinon."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def avatar_file(profile_dir: Optional[Path]) -> Optional[Path]:
    """Le visage du profil : le premier `assets/avatar.*` qui est un fichier ordinaire (ni lien, ni
    dossier) de 2 Mo au plus, dans un dossier `assets` qui n'est pas un lien. None sinon."""
    if profile_dir is None:
        return None
    assets = Path(profile_dir) / "assets"
    try:
        if not stat.S_ISDIR(os.lstat(assets).st_mode):
            return None
    except OSError:
        return None
    for name in AVATAR_NAMES:
        info = _regular(assets / name)
        if info is not None:
            return assets / name if info.st_size <= MAX_AVATAR_BYTES else None
    return None


def avatar_updated_at(profile_dir: Optional[Path]) -> Optional[float]:
    """La date du visage (celle du fichier), que l'app compare pour le relire ; None sans visage."""
    path = avatar_file(profile_dir)
    info = _regular(path) if path is not None else None
    return info.st_mtime if info is not None else None


def read_avatar(profile_dir: Optional[Path]) -> Optional[Tuple[bytes, str]]:
    """Les octets du visage et leur type, ou None : pas de fichier, trop gros, ou pas une image.
    Ouvert sans suivre de lien et relu borné : un fichier remplacé entre-temps ne passe pas."""
    path = avatar_file(profile_dir)
    if path is None:
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        data = handle.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        return None
    kind = image_type(data[:16])
    return (data, kind) if kind is not None else None


# Le titre de la session canonique d'un bot (tools/bot_mode_probe.py, BOT_CHAT_TITLE).
BOT_CHAT_TITLE = "Bot Chat"


class BotChatLinks(SqliteStore):
    """La session « Bot Chat » vers laquelle pointe la clé de session d'un chat d'agent de Sheldon.
    Pas de ligne : le chat d'aujourd'hui, sa propre session."""

    SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_chat_links (
    conversation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    linked_at REAL NOT NULL
);
"""

    def get(self, conversation_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM bot_chat_links WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        return row["session_id"] if row is not None else None

    def set(self, conversation_id: str, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO bot_chat_links (conversation_id, session_id, linked_at) VALUES (?, ?, ?)",
                (conversation_id, session_id, self._clock()),
            )

    def clear(self, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bot_chat_links WHERE conversation_id = ?", (conversation_id,))


def plan_link(*, multiplex: bool, bot_managed: bool, bot_chat: Optional[str], linked: Optional[str]) -> Optional[str]:
    """La session vers laquelle faire pointer le chat, ou None : rien à faire (déjà branché), ou le
    repli (pas de multiplex, profil non géré par Bot Mode, pas de « Bot Chat »)."""
    if multiplex and bot_managed and bot_chat and bot_chat != linked:
        return bot_chat
    return None


# La signature qu'Hermes pose devant un message d'un bot à un autre (tools/bot_mode_dm.py,
# message_agent) : « Message from 🤖 <nom> (@<handle>): <texte> ». Le handle est l'id du profil
# (hermes_constants.PROFILE_ID_RE), « hermes » pour le profil par défaut (_handle).
_PEER_MESSAGE = re.compile(r"Message from 🤖 (?P<name>[^\n]{1,80}?) \(@(?P<handle>[a-z0-9][a-z0-9_-]{0,63})\): (?P<text>.*)\Z", re.S)


def peer_author(content: str) -> Optional[Tuple[Dict[str, str], str]]:
    """L'auteur d'un message d'un autre bot ({name, agentId}) et son texte sans la signature, ou
    None : un message ordinaire."""
    match = _PEER_MESSAGE.match(content)
    if match is None or not match.group("text").strip():
        return None
    handle = match.group("handle")
    return {"name": match.group("name").strip(), "agentId": "default" if handle == "hermes" else handle}, match.group("text")
