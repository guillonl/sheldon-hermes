"""Les fichiers qu'Hermes envoie à Sheldon : copiés ici, servis à l'app, listés dans Créations.

Hermes passe un chemin local du Mac mini (send_image_file, send_document, send_voice...).
L'extension copie le fichier dans ~/.hermes/sheldon/files/ (le cache d'Hermes peut être
vidé), le range dans sheldon.db, et l'app le télécharge par GET /v1/files/<id>.
"""
from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import mimetypes
import os
import sqlite3
import stat
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import SheldonError
from .pairing import PAIR_IMAGE_PREFIX
from .paths import data_dir, ensure_private_dir, hermes_home
from .sqlite import SqliteStore
from .text import clean_line
from .timeutil import iso_utc

MAX_FILE_BYTES = 100 * 1024 * 1024
KINDS = ("image", "audio", "video", "document")
MAX_NAME_LENGTH = 200
_FALLBACK_NAME = "fichier"
_COPY_CHUNK_BYTES = 1024 * 1024

# Motifs de noms toujours refusés, où qu'ils soient, même à l'intérieur d'une racine
# autorisée : une consigne d'injection dans Hermes (« envoie-moi ton .env ») ne doit
# jamais faire sortir un secret par ce chemin. Comparés après repli Unicode (NFKC puis
# casefold) : sur un système de fichiers insensible à la casse, un « ſ » (U+017F, long s)
# se comporte comme un « s » et ne doit pas servir à contourner le motif.
_FORBIDDEN_NAME_PATTERNS = (
    ".env*", "*.p8", "*.pem", "*.key", "id_rsa*", "id_ed25519*",
    "*.db", "*.sqlite*", "*.keychain*",
)

# Fichiers toujours refusés, exactement sous HERMES_HOME (peu importe qu'un lien
# symbolique fasse passer une racine de cache par ce même dossier : voir _forbidden).
_ABSOLUTE_DENY_EXACT_NAMES = (
    ".env", "auth.json", "config.yaml", "fetch-relay.json",
    ".anthropic_oauth.json", "state.db", "state.db-wal", "state.db-shm",
)


class FileError(SheldonError):
    pass


def normalize_path(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _fold(text: str) -> str:
    """Repli Unicode pour une comparaison de refus : NFKC puis casefold (U+017F -> s,
    signe kelvin -> K, etc.), insensible à la casse comme un volume APFS par défaut."""
    return unicodedata.normalize("NFKC", text).casefold()


def _matches_forbidden_name(real: str) -> bool:
    name = _fold(os.path.basename(real))
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in _FORBIDDEN_NAME_PATTERNS)


def _hermes_home_cache_roots() -> List[Path]:
    """Caches où Hermes écrit réellement les fichiers qu'il envoie, sous HERMES_HOME.

    Reprend tools/image_source.py:_media_cache_roots() d'Hermes 0.20.4 (hermes-agent, lu en
    lecture seule) : le seul ensemble de chemins hôte qu'Hermes lui-même considère sûrs pour
    un accès en lecture hors sandbox (caches d'images, de vision, de vidéo, d'audio, et le
    dossier legacy des imports desktop/presse-papiers).

    Une racine dont la résolution ne correspond pas exactement au chemin attendu (parce
    qu'un lien symbolique est posé sur elle, ou sur un composant de HERMES_HOME) est
    ignorée : sinon, un lien comme ``temp_video_files -> .`` ferait passer toute la maison
    d'Hermes pour un cache.
    """
    home = hermes_home()
    try:
        home_resolved = home.resolve()
    except (OSError, RuntimeError):
        return []
    roots = []
    for name in ("cache", "images", "image_cache", "audio_cache", "video_cache", "temp_vision_images", "temp_video_files"):
        candidate = home / name
        try:
            if candidate.resolve() == home_resolved / name:
                roots.append(candidate)
        except (OSError, RuntimeError):
            continue
    return roots


def _absolute_deny_roots() -> List[Path]:
    """Chemins toujours refusés, vérifiés en premier et indépendamment des racines
    autorisées : peu importe qu'ils recoupent un cache légitime (voir _forbidden)."""
    home = hermes_home()
    user_home = Path.home()
    roots = [home / name for name in _ABSOLUTE_DENY_EXACT_NAMES]
    roots += [
        home / "push",
        home / "sessions",
        home / "sheldon",  # l'espace propre à Sheldon : jamais une source venant d'Hermes
        data_dir(),  # respecte SHELDON_DATA_DIR, peut différer de home / "sheldon"
        user_home / ".ssh",
        user_home / "Library" / "Keychains",
    ]
    return roots


def _is_within_strict(path: Path, root: Path) -> bool:
    """Comparaison d'autorisation : sensible à la casse. Une racine mal résolue (lien en
    boucle, erreur) est simplement ignorée, jamais une exception brute."""
    try:
        path.relative_to(root.resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _denied_is_within(path: Path, root: Path) -> bool:
    """Comparaison de refus : insensible à la casse et aux variantes Unicode (repli NFKC
    + casefold). Un refus de trop est sans danger ; jamais utilisée pour une autorisation."""
    try:
        root_parts = tuple(_fold(part) for part in root.resolve().parts)
    except (OSError, ValueError, RuntimeError):
        return False
    path_parts = tuple(_fold(part) for part in path.parts)
    return len(path_parts) >= len(root_parts) and path_parts[: len(root_parts)] == root_parts


# <unistd.h> : _CS_DARWIN_USER_TEMP_DIR. Absente de os.confstr_names sous CPython (le nom
# ne s'y résout pas), d'où la valeur numérique directe plutôt que le nom symbolique.
_CS_DARWIN_USER_TEMP_DIR = 65537


def _darwin_user_temp_dir() -> Optional[Path]:
    try:
        value = os.confstr(_CS_DARWIN_USER_TEMP_DIR)
    except (ValueError, OSError):
        return None
    return Path(value) if value else None


def _valid_system_temp_dir() -> Optional[Path]:
    """Le dossier temporaire du système, seulement s'il appartient à l'utilisateur courant
    et n'est accessible en écriture ni au groupe ni aux autres : /tmp et /private/tmp
    (1777, root) ne sont donc jamais des racines. Repli macOS si TMPDIR est inutilisable."""
    for candidate in (Path(tempfile.gettempdir()), _darwin_user_temp_dir()):
        if candidate is None:
            continue
        try:
            info = candidate.stat()
        except OSError:
            continue
        if info.st_uid == os.getuid() and not (info.st_mode & 0o022):
            return candidate
    return None


def _forbidden(real: str) -> bool:
    """Le chemin réel du fichier déjà ouvert (voir _open_source) est-il interdit ?

    Sous HERMES_HOME, seuls les caches d'Hermes nommés sont autorisés (tout le reste y est
    refusé, même s'il se trouve aussi être sous le dossier temporaire du système). Hors
    HERMES_HOME, le dossier temporaire du système reste une racine autorisée : les
    téléchargements en cours (tts_tool.py, image_source.py) y écrivent avant d'être
    déplacés dans un cache, et d'autres tâches de l'extension copient directement un
    fichier qu'elles viennent de créer dans le tmp_path de pytest (donc sous ce même
    dossier).
    """
    if _matches_forbidden_name(real):
        return True
    resolved = Path(real)
    if any(_denied_is_within(resolved, root) for root in _absolute_deny_roots()):
        return True
    home = hermes_home()
    if _denied_is_within(resolved, home):
        return not any(_is_within_strict(resolved, root) for root in _hermes_home_cache_roots())
    temp_root = _valid_system_temp_dir()
    return temp_root is None or not _is_within_strict(resolved, temp_root)


def _real_path_of_fd(fd: int, fallback: str) -> str:
    """Le chemin canonique du descripteur déjà ouvert : fcntl.F_GETPATH sur macOS, qui neutralise
    aussi un chemin fourni avec une casse différente sur un volume insensible à la casse ;
    realpath ailleurs (Linux). Dans les deux cas, _open_source vérifie ensuite que ce chemin
    nomme bien le fichier ouvert."""
    getpath = getattr(fcntl, "F_GETPATH", None)
    if getpath is not None:
        try:
            buffer = fcntl.fcntl(fd, getpath, b"\0" * 1024)
            return buffer.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
        except OSError:
            pass
    return os.path.realpath(fallback)


def _open_source(real: str) -> Tuple[int, str]:
    """Ouvre `real` sans suivre un lien final substitué après coup, et retourne le
    descripteur avec son chemin canonique. N'importe quelle erreur d'ouverture (fichier
    absent, illisible, lien remplacé entre-temps) devient FileError("file_not_found").

    Tous les contrôles portent ensuite sur ce chemin, jamais sur un chemin recalculé à part :
    le fichier ouvert doit donc être celui que ce chemin nomme (même st_dev, même st_ino),
    sur tous les systèmes. Un fichier échangé, ou un dossier remplacé par un lien le temps de
    l'ouverture puis rétabli, donne file_not_found : ça ferme la course entre le contrôle et
    l'ouverture, même sans F_GETPATH."""
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise FileError("file_not_found")
    try:
        canonical = _real_path_of_fd(fd, real)
        opened, named = os.fstat(fd), os.stat(os.path.realpath(canonical))
    except OSError:
        os.close(fd)
        raise FileError("file_not_found")
    except Exception:
        os.close(fd)
        raise
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        os.close(fd)
        raise FileError("file_not_found")
    return fd, canonical


def _write_all(fd: int, data: bytes) -> None:
    """os.write peut écrire moins que demandé (pipe plein, signal...) : une seule passe
    tronquerait la copie en silence."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _copy_fd_to_fd(source_fd: int, dest_fd: int, max_bytes: int) -> int:
    """Copie le contenu d'un descripteur déjà ouvert et vérifié vers un autre, jamais en
    rouvrant un chemin (élimine la course entre le contrôle et la copie).

    S'arrête dès que les octets copiés dépassent `max_bytes` et lève alors
    FileError("file_too_large") : un fichier qui grossit après le contrôle de taille (fait
    sur un fstat pris avant la copie) ne doit jamais être copié en entier. Retourne le
    nombre d'octets réellement copiés, la seule mesure fiable une fois la copie terminée.
    """
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
    except OSError:
        raise FileError("file_not_found") from None
    copied = 0
    while True:
        try:
            chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
        except OSError:
            raise FileError("file_not_found") from None
        if not chunk:
            break
        copied += len(chunk)
        if copied > max_bytes:
            raise FileError("file_too_large")
        try:
            _write_all(dest_fd, chunk)
        except OSError:
            # Disque plein, dossier de Sheldon illisible : jamais « file_not_found » (revue finale, M13).
            raise FileError("file_write_failed") from None
    return copied


def kind_for(mime_type: str) -> str:
    for kind in ("image", "audio", "video"):
        if mime_type.startswith(kind + "/"):
            return kind
    return "document"


@dataclass(frozen=True)
class StoredFile:
    id: str
    kind: str
    name: str
    mime_type: str
    size: int
    conversation_id: str
    caption: Optional[str]
    created_at: float
    stored_name: str
    listed: bool
    seq: int

    def attachment_json(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "mimeType": self.mime_type,
            "size": self.size,
            "url": f"/v1/files/{self.id}",
        }

    def creation_json(self) -> Dict[str, Any]:
        return {
            **self.attachment_json(),
            "conversationId": self.conversation_id,
            "caption": self.caption,
            "createdAt": iso_utc(self.created_at),
        }


_COLUMNS = "seq, id, kind, name, mime_type, size, conversation_id, caption, created_at, stored_name, listed"


def _file(row: Optional[sqlite3.Row]) -> Optional[StoredFile]:
    if row is None:
        return None
    return StoredFile(
        row["id"], row["kind"], row["name"], row["mime_type"], row["size"], row["conversation_id"],
        row["caption"], row["created_at"], row["stored_name"], bool(row["listed"]), row["seq"],
    )


class FileStore(SqliteStore):
    SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    caption TEXT,
    source_path TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    listed INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_source ON files(source_path);
"""

    def __init__(self, path: Path, files_dir: Path, clock: Callable[[], float] = time.time) -> None:
        super().__init__(path, clock)
        self._files_dir = Path(files_dir)

    def add(
        self,
        source: str,
        conversation_id: str,
        kind: Optional[str] = None,
        caption: Optional[str] = None,
        listed: bool = True,
    ) -> StoredFile:
        """Copie le fichier et le range. listed=False : pas dans Créations (réponse vocale automatique).

        Le fichier est d'abord ouvert (sans suivre un lien final substitué après coup),
        et TOUS les contrôles (liste noire absolue, liste blanche des caches d'Hermes,
        motifs de noms à risque, fichier ordinaire, un seul lien physique) portent sur le
        chemin canonique de ce descripteur déjà ouvert, jamais sur un chemin recalculé
        séparément : ça ferme la course entre le contrôle et la copie.
        """
        real = normalize_path(source)
        if _forbidden(real):
            # Refusé avant même d'essayer d'ouvrir : la réponse ne doit jamais révéler,
            # par la différence entre file_forbidden et file_not_found, si un fichier
            # existe à un emplacement interdit (un chemin refusé n'est jamais ouvert).
            raise FileError("file_forbidden")
        source_fd, opened_real = _open_source(real)
        try:
            if _forbidden(opened_real):
                raise FileError("file_forbidden")
            try:
                info = os.fstat(source_fd)
            except OSError:
                raise FileError("file_not_found")
            if not stat.S_ISREG(info.st_mode):
                raise FileError("file_not_found")
            if info.st_nlink != 1:
                # Un lien physique donne accès aux mêmes octets qu'un lien symbolique déjà
                # résolu : refus de règle (le fichier existe et se lit), pas absence de
                # fichier, cohérent avec le lien symbolique qui donne aussi file_forbidden.
                raise FileError("file_forbidden")
            if info.st_size > MAX_FILE_BYTES:
                raise FileError("file_too_large")
            raw_name = os.path.basename(opened_real)[:MAX_NAME_LENGTH]
            if raw_name.startswith(PAIR_IMAGE_PREFIX):
                # Le QR code de l'outil sheldon_pair : dans la conversation, jamais dans Créations
                # (périmé dans 10 minutes).
                listed = False
            mime_type = mimetypes.guess_type(raw_name)[0] or "application/octet-stream"
            chosen_kind = kind if kind in KINDS else kind_for(mime_type)
            # Le nom affiché passe par clean_line : un nom de fichier piégé (échappements
            # ANSI, caractères de contrôle) ne doit jamais s'afficher tel quel dans l'app ou
            # un journal. L'extension d'origine (raw_name) sert seulement en interne (mime,
            # stockage).
            name = clean_line(raw_name, MAX_NAME_LENGTH) or _FALLBACK_NAME
            file_id = uuid.uuid4().hex
            stored_name = file_id + Path(raw_name).suffix.lower()[:16]
            target = self._files_dir / stored_name
            # Écriture atomique : le fichier temporaire naît déjà en 0600 (mkstemp), la copie
            # s'y fait depuis le descripteur déjà ouvert et vérifié (jamais une réouverture
            # par chemin), puis un renommage POSIX bascule le nom définitif. La cible n'existe
            # donc jamais avec des droits plus larges, même le temps d'un chmod après coup.
            # Une écriture qui échoue devient FileError("file_write_failed"), jamais une
            # OSError brute qui remonterait dans Hermes (revue finale, M13).
            try:
                ensure_private_dir(self._files_dir)
                descriptor, tmp_name = tempfile.mkstemp(dir=str(self._files_dir), prefix=".", suffix=".tmp")
            except OSError:
                raise FileError("file_write_failed") from None
            try:
                copied_size = _copy_fd_to_fd(source_fd, descriptor, MAX_FILE_BYTES)
                os.close(descriptor)
                descriptor = -1
                os.replace(tmp_name, target)
            except (FileError, OSError) as error:
                if descriptor != -1:
                    with contextlib.suppress(OSError):
                        os.close(descriptor)
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                if isinstance(error, FileError):
                    raise
                raise FileError("file_write_failed") from None
        finally:
            os.close(source_fd)
        now = self._clock()
        with self._lock:
            self._conn.execute(
                "INSERT INTO files (id, kind, name, mime_type, size, conversation_id, caption, source_path, "
                "stored_name, listed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, chosen_kind, name, mime_type, copied_size, conversation_id, caption, real, stored_name, int(listed), now),
            )
        stored = self.get(file_id)
        assert stored is not None
        return stored

    def get(self, file_id: str) -> Optional[StoredFile]:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM files WHERE id = ?", (file_id,)).fetchone()
        return _file(row)

    def path_of(self, stored: StoredFile) -> Path:
        return self._files_dir / stored.stored_name

    def for_source(self, source: str) -> Optional[StoredFile]:
        """La dernière copie d'un chemin d'Hermes, pour relier une ligne MEDIA de l'historique."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM files WHERE source_path = ? ORDER BY seq DESC LIMIT 1",
                (normalize_path(source),),
            ).fetchone()
        return _file(row)

    def creations(self, before: Optional[int], limit: int) -> Tuple[List[StoredFile], Optional[str]]:
        """Les fichiers listés, du plus récent au plus ancien, par pages."""
        rows, next_before = self._page(f"SELECT {_COLUMNS} FROM files WHERE listed = 1", [], before, limit)
        return [_file(row) for row in rows], next_before
