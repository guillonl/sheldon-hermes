"""Où l'extension range ses fichiers et sur quels ports elle écoute."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LOCAL_PORT = 8787
DEFAULT_PUBLIC_PORT = 8443


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def data_dir() -> Path:
    override = os.environ.get("SHELDON_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return _hermes_home() / "sheldon"


def hermes_home() -> Path:
    """HERMES_HOME lui-même (pas le sous-dossier sheldon).

    Sert à vérifier qu'un fichier envoyé par Hermes vient bien d'un de ses caches
    (voir core/files.py) : cette vérification a besoin de la racine, pas de data_dir().
    """
    return _hermes_home()


def db_path() -> Path:
    return data_dir() / "sheldon.db"


def hermes_state_db() -> Path:
    return _hermes_home() / "state.db"


def ensure_private_dir(path: Path) -> Path:
    """Crée le dossier en 0o700, ou le resserre s'il existe avec des droits plus larges.

    Les fichiers -wal et -shm de SQLite y naissent avec les droits par défaut : c'est le
    dossier qui les protège.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def enabled_marker() -> Path:
    return data_dir() / "enabled"


def welcome_note() -> Path:
    """La conversation où envoyer le QR code au redémarrage qui suit l'installation (welcome.py)."""
    return data_dir() / "welcome.json"


def is_enabled() -> bool:
    return enabled_marker().exists()


def enable() -> None:
    ensure_private_dir(data_dir())
    enabled_marker().touch()


def disable() -> None:
    enabled_marker().unlink(missing_ok=True)


def _port(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw.isdigit():
        return default
    value = int(raw)
    return value if 1 <= value <= 65535 else default


def local_port() -> int:
    return _port("SHELDON_PORT", DEFAULT_LOCAL_PORT)


def public_port() -> int:
    return _port("SHELDON_PUBLIC_PORT", DEFAULT_PUBLIC_PORT)


def files_dir() -> Path:
    """Copies des fichiers qu'Hermes envoie (images, documents, audio), servies à l'app."""
    return data_dir() / "files"


def apns_dir() -> Path:
    """Clé .p8 d'Apple et réglages des notifications, jamais dans .env."""
    return data_dir() / "apns"
