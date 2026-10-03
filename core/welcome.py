"""Le QR code d'une première installation demandée dans un chat, envoyé seul au redémarrage (E40).

L'utilisateur colle à Hermes « Installe le plugin Sheldon…, puis envoie-moi le QR code de Sheldon ».
Hermes ne charge une extension qu'au redémarrage de son gateway (hermes_cli/plugins_cmd.py,
« Restart the gateway for the plugin to take effect ») : pendant l'installation, l'outil
sheldon_pair n'existe pas encore, et après /restart le modèle n'a pas de tour. D'où ce
mécanisme, une seule fois :
1. pendant l'installation, Hermes lance « hermes sheldon pair --after-restart » dans son
   terminal : la commande note la plateforme et le chat de la session, que le terminal
   d'Hermes transmet (tools/environments/local.py, _inject_session_context_env) ;
2. L'utilisateur envoie /restart dans ce chat : Hermes y écrit lui-même la plateforme et le chat de
   l'expéditeur, un utilisateur autorisé, dans .restart_notify.json
   (gateway/slash_commands.py) ;
3. au démarrage, l'adaptateur lit la note (et l'efface, quoi qu'il arrive) et ce fichier,
   avant qu'Hermes ne l'efface après son propre message de redémarrage (gateway/run.py,
   _send_restart_notification, appelé une fois tous les adaptateurs connectés) ;
4. si les deux désignent le même chat, d'une messagerie de la liste, dans les 30 minutes,
   l'extension fait l'offre de l'outil (Sheldon activé, aucun appareil encore relié, Serve
   présent, Funnel fermé, une seule offre, plafond horaire partagé) et envoie elle-même
   l'image et la phrase dans ce chat, par l'adaptateur de sa plateforme.
Le modèle ne voit ni le code, ni même le chemin de l'image. Tout écart (autre chat, trop tard,
fichier illisible, redémarrage par le terminal) n'envoie rien : l'utilisateur demande alors « QR code ».
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional, Tuple

from .messages import TEXTS
from .pair_link import ServeCheck, pair_image
from .pairing import PAIR_CODE_TTL_SECONDS
from .paths import ensure_private_dir
from .runtime import CHAT_PLATFORMS, ToolContext
from .store import DeviceStore

logger = logging.getLogger(__name__)

WELCOME_TTL_SECONDS = 30 * 60
# Le fichier qu'Hermes écrit dans HERMES_HOME quand /restart part d'un chat.
RESTART_NOTICE = ".restart_notify.json"
# L'image part après le message « Gateway restarted » d'Hermes, que l'on attend au plus ce délai.
NOTICE_WAIT_SECONDS = 30.0
NOTICE_POLL_SECONDS = 0.5
_MAX_FILE_BYTES = 64 * 1024
_LANGUAGES = ("fr", "en")


@dataclass(frozen=True)
class WelcomeTarget:
    platform: str
    chat_id: str
    thread_id: str = ""


@dataclass(frozen=True)
class WelcomeNote:
    target: WelcomeTarget
    language: str
    created_at: float


def _write_private_json(path: Path, data: Mapping[str, Any]) -> None:
    ensure_private_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".welcome-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _MAX_FILE_BYTES:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _text(value: Any) -> Optional[str]:
    """Un identifiant de plateforme, de chat ou de fil : texte (ou entier) non vide."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def run_note(
    env: Mapping[str, str],
    *,
    enabled: bool,
    store: DeviceStore,
    path: Path,
    language: str,
    now: float,
    out: Callable[[str], Any],
) -> int:
    """« hermes sheldon pair --after-restart » : note le chat de l'installation, dans le terminal
    d'Hermes (env : les variables de la session). Jamais de code ni d'image ici."""
    texts = TEXTS[language]
    if not enabled:
        out(texts["welcome_disabled"])
        return 1
    context = ToolContext(
        platform=env.get("HERMES_SESSION_PLATFORM", ""),
        chat_id=env.get("HERMES_SESSION_CHAT_ID", ""),
        cron_platform=env.get("HERMES_CRON_AUTO_DELIVER_PLATFORM", ""),
        cron_chat_id=env.get("HERMES_CRON_AUTO_DELIVER_CHAT_ID", ""),
        cron_session=env.get("HERMES_CRON_SESSION", ""),
    )
    # Mêmes messageries que sheldon_pair (runtime.CHAT_PLATFORMS), jamais en tâche planifiée.
    if not context.opened_by_a_message():
        out(texts["welcome_not_here"])
        return 1
    # Une première installation seulement : ensuite, « le QR code de Sheldon » suffit.
    if store.list_devices():
        out(texts["welcome_already_paired"])
        return 1
    # Le fil n'est pas noté : l'image part dans celui d'où /restart a été envoyé.
    _write_private_json(path, {"platform": context.platform, "chatId": context.chat_id, "language": language, "createdAt": now})
    out(texts["welcome_noted"].format(minutes=WELCOME_TTL_SECONDS // 60))
    return 0


def _take_note(path: Path) -> Optional[WelcomeNote]:
    """La note, effacée aussitôt lue : une seule tentative, quoi qu'il arrive ensuite."""
    data = _read_json(path)
    path.unlink(missing_ok=True)
    if not isinstance(data, dict):
        return None
    platform, chat_id = _text(data.get("platform")), _text(data.get("chatId"))
    created_at, language = data.get("createdAt"), data.get("language")
    if platform is None or chat_id is None or language not in _LANGUAGES:
        return None
    if isinstance(created_at, bool) or not isinstance(created_at, (int, float)) or not math.isfinite(created_at):
        return None
    return WelcomeNote(WelcomeTarget(platform, chat_id), language, float(created_at))


def _restart_chat(hermes_home: Path) -> Optional[WelcomeTarget]:
    """Le chat d'où l'utilisateur a envoyé /restart, tel qu'Hermes l'a écrit ; le fichier reste à Hermes."""
    data = _read_json(Path(hermes_home) / RESTART_NOTICE)
    if not isinstance(data, dict):
        return None
    platform, chat_id = _text(data.get("platform")), _text(data.get("chat_id"))
    if platform is None or chat_id is None:
        return None
    return WelcomeTarget(platform, chat_id, _text(data.get("thread_id")) or "")


def take_target(note_path: Path, hermes_home: Path, now: float) -> Optional[WelcomeNote]:
    """Au démarrage du gateway : la note de l'installation, si le chat de /restart est le même.

    Même plateforme, même chat, messagerie de la liste, note de moins de 30 minutes. L'image
    part dans le fil où /restart a été envoyé. La note est effacée dans tous les cas."""
    note = _take_note(Path(note_path))
    if note is None:
        return None
    restart = _restart_chat(hermes_home)
    if restart is None or (restart.platform, restart.chat_id) != (note.target.platform, note.target.chat_id):
        return None
    if note.target.platform not in CHAT_PLATFORMS or not 0 <= now - note.created_at < WELCOME_TTL_SECONDS:
        return None
    return WelcomeNote(restart, note.language, note.created_at)


def restart_notice_pending(hermes_home: Path) -> bool:
    return (Path(hermes_home) / RESTART_NOTICE).exists()


def prepare(
    *,
    store: DeviceStore,
    enabled: bool,
    check: Callable[[], ServeCheck],
    public_port: int,
    image_dir: Path,
    schedule: Optional[Callable[[float, Callable[[], None]], None]] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """Le QR code à envoyer, ou pourquoi il n'y en a pas : les contrôles de sheldon_pair, plus
    « aucun appareil relié ». Bloquant (Tailscale, /v1/health) : hors de la boucle du gateway."""
    if not enabled:
        return None, "Sheldon is disabled"
    if store.list_devices():
        return None, "a device is already linked"
    return pair_image(store=store, check=check(), public_port=public_port, image_dir=image_dir, schedule=schedule)


async def deliver(
    note: WelcomeNote,
    *,
    prepare: Callable[[], Tuple[Optional[Path], Optional[str]]],
    send_image: Callable[[WelcomeTarget, Path, str], Awaitable[bool]],
    notice_pending: Callable[[], bool],
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> bool:
    """Attend le message de redémarrage d'Hermes, fait l'offre, puis envoie l'image et la phrase.
    Rien ne lève : une erreur est journalisée, jamais avec le code (seul le PNG le porte)."""
    waited = 0.0
    while notice_pending() and waited < NOTICE_WAIT_SECONDS:
        await sleep(NOTICE_POLL_SECONDS)
        waited += NOTICE_POLL_SECONDS
    platform = note.target.platform
    try:
        image, refusal = await asyncio.to_thread(prepare)
        if image is None:
            logger.warning("Sheldon: welcome QR code not sent to %s: %s", platform, refusal)
            return False
        caption = TEXTS[note.language]["welcome_caption"].format(minutes=PAIR_CODE_TTL_SECONDS // 60)
        if not await send_image(note.target, image, caption):
            logger.warning("Sheldon: welcome QR code not delivered to %s", platform)
            return False
    except Exception:
        logger.warning("Sheldon: welcome QR code failed for %s", platform, exc_info=True)
        return False
    logger.info("Sheldon: welcome QR code sent to %s", platform)
    return True
