"""Les notifications de Sheldon, envoyées directement à Apple (APNs), sans relais.

La clé .p8 de Léo reste sur le Mac mini (~/.hermes/sheldon/apns/, droits 600). Le jeton
JWT est signé en ES256 (PyJWT, déjà dans le venv d'Hermes) et renouvelé toutes les 50
minutes (Apple le veut entre 20 et 60). Python n'a pas de client HTTP/2 dans ce venv :
la requête part par « curl --http2 », qui lit ses options sur l'entrée standard pour
que le jeton n'apparaisse jamais dans la liste des processus.

Ronde 1 (2026-09-24) : la clé n'est acceptée qu'après un contrôle strict de ses droits
(lien symbolique, lien physique, propriétaire, mode) et de sa courbe (EC P-256), vérifié
une seule fois au chargement, jamais à chaque envoi. La charge envoyée à Apple est bornée
à 4096 octets en cédant sur les textes, jusqu'à leurs planchers de texte lisible (une charge
faite surtout d'identifiants et de planchers peut, en théorie, rester au-dessus : Apple répond
alors 413, journalisé). Sans aperçu, même les identifiants de conversation deviennent opaques.

Ronde 2 (2026-09-24) : apns-expiration est absent par défaut (Apple stocke et réessaie),
pas 0 (une seule tentative) ; seul un appel VoIP futur voudra 0. Le bornage de la charge
borne d'abord les libellés des boutons, jamais vidés, avant de céder sur le corps puis le
titre, chacun gardant un plancher de texte lisible.

Tâche 14 (décision A54) : les identifiants opaques sans aperçu sont un HMAC-SHA256 salé
par push_key (32 octets aléatoires, store.push_key()), remplaçant le SHA-256 sans sel :
un tiers qui verrait la notification ne peut plus deviner l'identifiant réel, et seule
l'app qui détient sa copie de pushKey (reçue à l'appairage) retrouve la conversation
visée en recalculant la même empreinte. `push_key` est un mot-clé obligatoire sur
request_payload/reply_payload/task_payload : aucun appel ne peut l'omettre en silence.

Ronde de correction (lot 14-11) : push_key n'a plus de valeur par défaut.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Set, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .media import plain_preview
from .paths import ensure_private_dir
from .requests import Request
from .store import DeviceStore, PushTarget

logger = logging.getLogger(__name__)

APNS_HOSTS = {
    "production": "https://api.push.apple.com",
    # L'app lancée depuis Xcode reçoit ses notifications du bac à sable d'Apple.
    "sandbox": "https://api.sandbox.push.apple.com",
}
ENVIRONMENTS = tuple(APNS_HOSTS)
DEFAULT_TOPIC = "design.leoguillon.Sheldon"
CONFIG_NAME = "apns.json"
KEY_NAME = "AuthKey.p8"
TOKEN_LIFETIME = 50 * 60
CURL = "/usr/bin/curl"
_DEVICE_TOKEN = re.compile(r"(?:[0-9a-f]{2}){32,100}")
_KEY_ID = re.compile(r"[A-Z0-9]{10}")
# Un identifiant de bundle Apple : lettres, chiffres, points, tirets, de 1 à 60 caractères
# (constat Minor, relecture du lot 12-13, borne reprise en ronde 2). Sans ça, un topic piégé
# (retour à la ligne, guillemet) finirait dans un en-tête du fichier de config de curl.
_TOPIC = re.compile(r"[A-Za-z0-9.-]{1,60}")
DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}
# Un 403 avec cette raison veut dire qu'Apple a déjà rejeté le jeton en cache : le
# suivant doit être neuf, même si les 50 minutes ne sont pas encore passées.
RENEW_TOKEN_REASONS = {"ExpiredProviderToken"}
MAX_CHOICES_IN_PUSH = 4
# Apple refuse une charge de plus de 4096 octets (413 PayloadTooLarge).
MAX_PAYLOAD_BYTES = 4096
# Un libellé de bouton (« … » compris) : jamais plus long, même quand la charge tient déjà.
MAX_LABEL_LENGTH = 64
# Un champ qu'on ne vide jamais tout à fait : un bouton sans texte ou une question réduite
# à rien serait inutilisable sur l'écran verrouillé, même si la charge dépasse encore 4096
# octets une fois ce plancher atteint.
MIN_BODY_LENGTH = 120
MIN_FIELD_LENGTH = 8
# Une commande d'approbation plus longue que ça n'a pas sa place dans le push : l'app ne
# pose son bouton « Autoriser » direct que devant une commande tenant sur une seule ligne.
MAX_COMMAND_LENGTH = 200
_BEARER_IN_LOG = re.compile(r"(?i)bearer\s+\S+")

Runner = Callable[[List[str], bytes], Awaitable[Tuple[int, bytes, bytes]]]


class InsecureKeyError(Exception):
    """La clé .p8 ou son dossier n'offrent pas assez de garanties : notifications désactivées."""


class PushSetupError(ValueError):
    """Un réglage refusé par save_config. `code` choisit la phrase montrée à Léo (messages.py,
    push_setup_<code>) : jamais le texte d'une exception, qui mélangerait l'anglais au français."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_device_token(token: object) -> Optional[str]:
    if not isinstance(token, str):
        return None
    clean = token.strip().lower()
    return clean if _DEVICE_TOKEN.fullmatch(clean) else None


@dataclass(frozen=True)
class ApnsConfig:
    key_id: str
    team_id: str
    topic: str
    key_path: Path
    preview: bool = True


def load_config(directory: Path) -> Optional[ApnsConfig]:
    """Les réglages posés par « hermes sheldon push setup », ou None s'ils manquent."""
    config_path = Path(directory) / CONFIG_NAME
    key_path = Path(directory) / KEY_NAME
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not key_path.is_file():
        return None
    key_id, team_id, topic = data.get("keyId"), data.get("teamId"), data.get("topic", DEFAULT_TOPIC)
    if not all(isinstance(v, str) and v for v in (key_id, team_id, topic)):
        return None
    return ApnsConfig(key_id, team_id, topic, key_path, bool(data.get("preview", True)))


def _load_p256_key(pem: bytes, path: Path) -> ec.EllipticCurvePrivateKey:
    """La seule courbe qu'APNs accepte pour un jeton ES256 : rejeter tout de suite une clé
    RSA ou une autre courbe, plutôt que d'échouer en silence au premier envoi."""
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as error:
        raise InsecureKeyError(f"{path} n'est pas une clé .p8 lisible : notifications désactivées") from error
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise InsecureKeyError(f"{path} doit être une clé EC P-256 (secp256r1) : notifications désactivées")
    return key


def save_config(directory: Path, key_source: Path, key_id: str, team_id: str, topic: str = DEFAULT_TOPIC, preview: bool = True) -> ApnsConfig:
    if not _KEY_ID.fullmatch(key_id) or not _KEY_ID.fullmatch(team_id):
        raise PushSetupError("bad_ids")
    if not _TOPIC.fullmatch(topic):
        raise PushSetupError("bad_topic")
    try:
        text = Path(key_source).read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise PushSetupError("key_invalid") from error
    except OSError as error:
        raise PushSetupError("key_unreadable") from error
    if "BEGIN PRIVATE KEY" not in text:
        raise PushSetupError("key_invalid")
    try:
        _load_p256_key(text.encode("utf-8"), Path(key_source))
    except InsecureKeyError as error:
        raise PushSetupError("key_invalid") from error
    ensure_private_dir(Path(directory))
    key_path = Path(directory) / KEY_NAME
    for path, content in ((key_path, text), (Path(directory) / CONFIG_NAME, json.dumps(
        {"keyId": key_id, "teamId": team_id, "topic": topic, "preview": preview}, indent=2
    ) + "\n")):
        _write_private(path, content)
    return ApnsConfig(key_id, team_id, topic, key_path, preview)


def _write_private(path: Path, content: str) -> None:
    """Écrit `content` dans `path` en 0600 par un fichier temporaire créé sans suivre de
    lien et sans réutiliser une entrée laissée par un essai précédent (O_EXCL après avoir
    retiré un .tmp éventuel, lien symbolique compris : le retirer ne touche jamais sa
    cible), avec fsync avant le remplacement atomique."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with contextlib.suppress(OSError):
        os.unlink(temporary)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _check_private_directory(path: Path) -> None:
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise InsecureKeyError(f"{path} est introuvable ({error}) : notifications désactivées") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise InsecureKeyError(
            f"{path} doit être un dossier privé à ce compte (chmod 700 {path}) : notifications désactivées"
        )


def _read_private_file(path: Path) -> bytes:
    """Ouvre `path` en refusant tout lien (symbolique ou physique), toute appartenance à un
    autre compte et tout droit de groupe ou de tous. Le descripteur est ouvert avec
    O_NOFOLLOW puis inspecté par fstat, pour ne jamais lire au travers d'un lien posé entre
    la vérification et l'ouverture (TOCTOU)."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise InsecureKeyError(f"{path} est illisible ({error}) : notifications désactivées") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise InsecureKeyError(
                f"{path} doit être un fichier ordinaire, sans lien symbolique ni lien physique : "
                "notifications désactivées"
            )
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise InsecureKeyError(
                f"{path} doit être en 0600 et appartenir à ce compte (chmod 600 {path}) : notifications désactivées"
            )
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _curl_supports_http2(curl: str) -> bool:
    """Vérifié une seule fois, au chargement : sans HTTP/2, chaque envoi échouerait en
    silence (curl refuserait --http2 et send() rendrait toujours curl_failed)."""
    if not os.access(curl, os.X_OK):
        return False
    try:
        completed = subprocess.run([curl, "--version"], capture_output=True, timeout=5, text=True)
    except OSError:
        return False
    return completed.returncode == 0 and "HTTP2" in completed.stdout


class ApnsToken:
    def __init__(self, config: ApnsConfig, clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._clock = clock
        _check_private_directory(config.key_path.parent)
        pem = _read_private_file(config.key_path)
        self._key = _load_p256_key(pem, config.key_path)
        self._cached: Optional[Tuple[float, str]] = None

    def bearer(self) -> str:
        import jwt

        now = self._clock()
        # now < iat : l'horloge a reculé (redémarrage, correction NTP) depuis le dernier
        # jeton ; le garder serait daté d'un futur qui n'est jamais arrivé pour Apple.
        if self._cached is None or now < self._cached[0] or now - self._cached[0] >= TOKEN_LIFETIME:
            token = jwt.encode(
                {"iss": self._config.team_id, "iat": int(now)}, self._key, algorithm="ES256",
                headers={"kid": self._config.key_id},
            )
            self._cached = (now, token)
        return self._cached[1]

    def invalidate(self) -> None:
        """Un 403 ExpiredProviderToken d'Apple : le prochain bearer() en signera un neuf."""
        self._cached = None


@dataclass(frozen=True)
class Push:
    token: str
    environment: str
    payload: Dict[str, Any]
    push_type: str = "alert"
    collapse_id: Optional[str] = None
    # apns-expiration : l'échéance réelle d'une demande qui en a une ; None sinon (aucun
    # en-tête, Apple stocke et réessaie tant que l'appareil est hors ligne). 0 est réservé
    # à un appel VoIP (tâche 17) : un appel ne doit jamais sonner en retard.
    expiration: Optional[int] = None

    @property
    def priority(self) -> int:
        return 5 if self.push_type == "background" else 10


@dataclass(frozen=True)
class PushResult:
    status: int
    reason: Optional[str]

    @property
    def dead_token(self) -> bool:
        return self.status == 410 or self.reason in DEAD_TOKEN_REASONS


async def run_command(args: List[str], stdin: bytes) -> Tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin), timeout=30)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return -1, b"", b"timeout"
    except asyncio.CancelledError:
        # Un envoi annulé (arrêt du service, tour suivant) ne doit jamais laisser curl
        # tourner en arrière-plan.
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return process.returncode or 0, out, err


def _quote(value: str) -> str:
    # Guillemets du fichier de config de curl : seuls \ et " demandent un échappement.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _redact(text: str) -> str:
    """N'écrit jamais le jeton dans le journal : -q ignore déjà le curlrc de l'utilisateur,
    ceci protège aussi un stderr inattendu qui contiendrait l'en-tête envoyé."""
    return _BEARER_IN_LOG.sub("bearer ***", text)


def _payload_size(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _is_lone_surrogate(char: str) -> bool:
    return 0xD800 <= ord(char) <= 0xDFFF


def _without_lone_surrogates(value: Any) -> Any:
    """Un texte tronqué en amont (par Hermes, ou par un JSON mal recomposé) peut porter un
    demi-couple UTF-16 : un surrogate isolé ne s'encode pas en UTF-8 (UnicodeEncodeError),
    ce qui ferait perdre l'envoi entier. Remplacé par U+FFFD avant toute sérialisation."""
    if isinstance(value, str):
        if not any(_is_lone_surrogate(char) for char in value):
            return value
        return "".join("\N{REPLACEMENT CHARACTER}" if _is_lone_surrogate(char) else char for char in value)
    if isinstance(value, dict):
        return {key: _without_lone_surrogates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_lone_surrogates(item) for item in value]
    return value


def _trimmed(text: str, length: int) -> str:
    """`text` coupé à `length` caractères au plus (une « … » ajoutée si besoin), sans
    jamais couper une séquence UTF-8 (le retrait se fait par point de code) ni laisser un
    joker (ZWJ) esseulé à la nouvelle fin."""
    if len(text) <= length:
        return text
    cut = max(0, length - 1)
    while cut > 0 and text[cut - 1] == "\N{ZERO WIDTH JOINER}":
        cut -= 1
    return text[:cut].rstrip() + "…"


def _cap_labels(payload: Dict[str, Any]) -> bool:
    """Chaque libellé de bouton borné à MAX_LABEL_LENGTH, toujours, même si la charge
    tient déjà : un clarify d'Hermes n'a aucune borne sur ses libellés (requests.py).
    Rend vrai si au moins un libellé a été raccourci."""
    changed = False
    for choice in payload.get("sheldon", {}).get("choices") or []:
        label = choice.get("label")
        if isinstance(label, str):
            trimmed = _trimmed(label, MAX_LABEL_LENGTH)
            if trimmed != label:
                choice["label"] = trimmed
                changed = True
    return changed


def _shrink_with_floor(container: Dict[str, Any], key: str, payload: Dict[str, Any], floor: int) -> None:
    """Raccourcit container[key] jusqu'à ce que `payload` tienne dans MAX_PAYLOAD_BYTES,
    sans jamais descendre sous `floor` caractères : mieux vaut dépasser encore un peu la
    limite d'Apple qu'un bouton ou une question vidés. Cherche par dichotomie la plus
    longue version qui tient."""
    if _payload_size(payload) <= MAX_PAYLOAD_BYTES:
        return
    text = container.get(key)
    if not isinstance(text, str) or len(text) <= floor:
        return
    container[key] = _trimmed(text, floor)
    if _payload_size(payload) > MAX_PAYLOAD_BYTES:
        return
    low, high = floor, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        container[key] = _trimmed(text, mid)
        if _payload_size(payload) <= MAX_PAYLOAD_BYTES:
            low = mid
        else:
            high = mid - 1
    container[key] = _trimmed(text, low)


def _bounded(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Une charge de plus de 4096 octets serait rejetée par Apple (413 PayloadTooLarge).
    Les libellés des boutons sont d'abord bornés (toujours), puis, si la charge dépasse
    encore la limite, command disparaît en entier (jamais coupé : une commande tronquée ne
    doit jamais s'afficher à côté d'« Autoriser »), puis le corps cède (jamais sous
    MIN_BODY_LENGTH caractères), puis le titre (jamais sous MIN_FIELD_LENGTH). Travaille sur
    une copie : l'original de Push.payload est partagé entre tous les appareils visés par un
    même envoi."""
    choices = payload.get("sheldon", {}).get("choices")
    labels_too_long = isinstance(choices, list) and any(
        isinstance(choice.get("label"), str) and len(choice["label"]) > MAX_LABEL_LENGTH for choice in choices
    )
    if not labels_too_long and _payload_size(payload) <= MAX_PAYLOAD_BYTES:
        return payload
    payload = copy.deepcopy(payload)
    _cap_labels(payload)
    if _payload_size(payload) <= MAX_PAYLOAD_BYTES:
        return payload
    sheldon = payload.get("sheldon")
    if isinstance(sheldon, dict) and "command" in sheldon:
        del sheldon["command"]
        if _payload_size(payload) <= MAX_PAYLOAD_BYTES:
            return payload
    alert = payload.get("aps", {}).get("alert")
    if isinstance(alert, dict):
        _shrink_with_floor(alert, "body", payload, MIN_BODY_LENGTH)
        _shrink_with_floor(alert, "title", payload, MIN_FIELD_LENGTH)
    return payload


class ApnsClient:
    def __init__(self, config: ApnsConfig, token: ApnsToken, run: Runner = run_command, curl: str = CURL) -> None:
        self._config = config
        self._token = token
        self._run = run
        self._curl = curl

    def curl_config(self, push: Push) -> str:
        # Un appel (PushKit) part sur le sujet « .voip » de l'app, toujours avec apns-expiration
        # à 0 (tâche 17) : il ne doit jamais sonner en retard, quelle que soit push.expiration.
        voip = push.push_type == "voip"
        headers = [
            f"authorization: bearer {self._token.bearer()}",
            f"apns-topic: {self._config.topic}{'.voip' if voip else ''}",
            f"apns-push-type: {push.push_type}",
            f"apns-priority: {push.priority}",
        ]
        if voip:
            headers.append("apns-expiration: 0")
        elif push.expiration is not None:
            headers.append(f"apns-expiration: {push.expiration}")
        if push.collapse_id:
            headers.append(f"apns-collapse-id: {push.collapse_id[:64]}")
        lines = [f"url = {_quote(APNS_HOSTS[push.environment] + '/3/device/' + push.token)}"]
        lines += [f"header = {_quote(header)}" for header in headers]
        body = json.dumps(_bounded(_without_lone_surrogates(push.payload)), ensure_ascii=False, separators=(",", ":"))
        lines.append(f"data-binary = {_quote(body)}")
        return "\n".join(lines) + "\n"

    async def send(self, push: Push) -> PushResult:
        args = [
            self._curl, "-q", "--http2", "--silent", "--show-error", "--max-time", "15",
            "--output", "-", "--write-out", "\\n%{http_code}", "--config", "-",
        ]
        code, out, err = await self._run(args, self.curl_config(push).encode("utf-8"))
        if code != 0:
            logger.warning("Sheldon: curl could not reach APNs (%s): %s", code, _redact(err.decode("utf-8", "replace").strip()))
            return PushResult(0, "curl_failed")
        body, _, status = out.decode("utf-8", "replace").rpartition("\n")
        reason = None
        if body.strip():
            try:
                data = json.loads(body)
                reason = data.get("reason") if isinstance(data, dict) else None
            except ValueError:
                reason = None
        result = PushResult(int(status) if status.strip().isdigit() else 0, reason)
        if result.status == 403 and result.reason in RENEW_TOKEN_REASONS:
            self._token.invalidate()
        return result


# Ce que contient chaque notification. Sans aperçu (preview false), rien du contenu ne
# passe par Apple : l'app traduit les clés loc-key dans sa langue, et les identifiants de
# conversation deviennent des empreintes opaques plutôt que des noms lisibles.


def _alert(title: str, body: str, preview: bool, kind: str) -> Dict[str, Any]:
    if preview:
        return {"title": title, "body": body}
    return {"title-loc-key": f"PUSH_{kind}_TITLE", "loc-key": f"PUSH_{kind}_BODY"}


def _opaque(value: str, push_key: bytes) -> str:
    """Une empreinte courte, jamais un identifiant lisible : ce que l'app voit dans
    thread-id ou conversationId quand Léo a coupé l'aperçu. HMAC-SHA256 salé par
    push_key (décision A54, remplace le SHA-256 sans sel) : stable pour une même clé,
    différente d'une clé à l'autre, pour que seule l'app qui détient sa copie de
    pushKey (reçue à l'appairage) puisse la retrouver en la recalculant."""
    return hmac.new(push_key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def request_payload(request: Request, sender: str, preview: bool, badge: int, *, push_key: bytes) -> Dict[str, Any]:
    data = request.to_json()
    aps: Dict[str, Any] = {
        "alert": _alert(sender, plain_preview(request.title), preview, "REQUEST"),
        "sound": "default",
        "category": "sheldon.request",
        # Toutes les demandes ensemble sur l'écran verrouillé.
        "thread-id": "requests",
        "badge": badge,
        "mutable-content": 1,
        # Une proposition n'attend rien dans Hermes : seules les demandes bloquantes
        # (clarify, approbation) justifient de traverser les modes Concentration.
        "interruption-level": "time-sensitive" if request.blocking else "active",
    }
    if preview and request.category and request.category.strip().casefold() != sender.strip().casefold():
        # Pas de sous-titre qui redit le titre (une demande de l'agent « Calendrier » classée « Calendrier »).
        aps["alert"]["subtitle"] = request.category
    info: Dict[str, Any] = {
        "type": "request", "requestId": request.id,
        "conversationId": request.conversation_id if preview else _opaque(request.conversation_id, push_key),
        "kind": request.kind, "allowsText": request.allows_text, "expiresAt": data["expiresAt"],
    }
    if preview:
        info["choices"] = data["choices"][:MAX_CHOICES_IN_PUSH]
        # Le bouton « Autoriser » direct de l'app n'apparaît que si elle peut montrer la
        # commande entière et fidèle : jamais coupée ni retouchée, telle qu'Hermes l'a
        # envoyée (déjà masquée, request.command). Sans aperçu, jamais de command
        # (décision A54) : rien de lisible ne passe chez Apple.
        if request.kind == "approval" and request.command and len(request.command) <= MAX_COMMAND_LENGTH:
            info["command"] = request.command
    return {"aps": aps, "sheldon": info}


def closed_payload(request_id: str) -> Dict[str, Any]:
    # Une notification silencieuse ne porte que content-available : l'app recompte le badge au réveil.
    return {"aps": {"content-available": 1}, "sheldon": {"type": "request.closed", "requestId": request_id}}


# Une réponse faite d'un seul bloc Markdown (```sheldon```) ou d'un seul fichier n'a aucun
# texte lisible (plain_preview vide) : jamais de corps vide, la clé de l'app PUSH_REPLY_VISUAL
# ou PUSH_REPLY_FILE prend sa place, traduite en français ou en anglais par l'app, jamais un
# texte français en dur (revue finale, M10). Sans aperçu, le nom du fichier ne doit jamais
# atteindre Apple : PUSH_REPLY_VISUAL part sans argument comme avec l'aperçu, mais PUSH_REPLY_FILE
# porte un « %@ » qui resterait alors vide, d'où PUSH_REPLY_FILE_ANON, une clé à part sans
# argument (pré-vol du plan 4 de l'app), comme PUSH_DEVICE_BODY_ANON pour un nouvel appareil.


def reply_payload(
    conversation_id: str, sender: str, text: str, preview: bool, file_name: Optional[str] = None, *, push_key: bytes
) -> Dict[str, Any]:
    thread = conversation_id if preview else _opaque(conversation_id, push_key)
    body = plain_preview(text)
    if preview:
        alert: Dict[str, Any] = {"title": sender}
        if body:
            alert["body"] = body
        elif file_name:
            alert.update({"loc-key": "PUSH_REPLY_FILE", "loc-args": [file_name]})
        else:
            alert["loc-key"] = "PUSH_REPLY_VISUAL"
    else:
        if body:
            key = "PUSH_REPLY_BODY"
        else:
            # PUSH_REPLY_FILE porte un « %@ » (le nom du fichier, avec aperçu) : sans aperçu,
            # sans argument, il faut une clé à part, sans « %@ » (pré-vol du plan 4 de l'app),
            # comme PUSH_DEVICE_BODY_ANON pour un nouvel appareil.
            key = "PUSH_REPLY_FILE_ANON" if file_name else "PUSH_REPLY_VISUAL"
        alert = {"title-loc-key": "PUSH_REPLY_TITLE", "loc-key": key}
    return {
        "aps": {
            "alert": alert,
            "sound": "default", "category": "sheldon.reply", "thread-id": thread, "mutable-content": 1,
        },
        "sheldon": {"type": "reply", "conversationId": thread},
    }


def task_payload(item: Dict[str, Any], preview: bool, *, push_key: bytes) -> Dict[str, Any]:
    conversation_id = item["conversationId"]
    return {
        "aps": {
            "alert": _alert(item["title"], item["summary"] or item["title"], preview, "TASK"),
            "sound": "default", "category": "sheldon.task", "thread-id": "feed", "mutable-content": 1,
        },
        "sheldon": {
            "type": "task", "feedItemId": item["id"],
            "conversationId": conversation_id if preview else _opaque(conversation_id, push_key),
        },
    }


def device_payload(device_id: str, name: str, preview: bool) -> Dict[str, Any]:
    # Texte de l'app (« Un nouvel appareil s'est relié : iPad ») : l'app le traduit ; sans aperçu, pas de nom.
    if preview:
        alert: Dict[str, Any] = {"title-loc-key": "PUSH_DEVICE_TITLE", "loc-key": "PUSH_DEVICE_BODY", "loc-args": [name]}
    else:
        alert = {"title-loc-key": "PUSH_DEVICE_TITLE", "loc-key": "PUSH_DEVICE_BODY_ANON"}
    return {
        "aps": {"alert": alert, "sound": "default", "category": "sheldon.device", "thread-id": "devices"},
        "sheldon": {"type": "device.paired", "deviceId": device_id},
    }


def _call_info(call: Dict[str, Any], preview: bool, push_key: bytes) -> Dict[str, Any]:
    # Pour l'affichage seulement, requested : l'extension est la seule à appliquer les garde-fous
    # (core/calls.py). Sans aperçu (décision A54) : ni nom d'agent, ni identifiant lisible, leurs
    # empreintes HMAC par pushKey, comme pour les autres pushes.
    if preview:
        return {
            "type": "call", "callId": call["callId"], "agentId": call["agentId"], "agentName": call["agentName"],
            "conversationId": call["conversationId"], "reason": plain_preview(call["reason"]), "requested": call["requested"],
        }
    return {
        "type": "call", "callId": call["callId"], "agentId": _opaque(call["agentId"], push_key),
        "conversationId": _opaque(call["conversationId"], push_key), "reason": "", "requested": call["requested"],
    }


def call_payload(call: Dict[str, Any], preview: bool, *, push_key: bytes) -> Dict[str, Any]:
    # Pas d'aps : PushKit remet tout à l'app, qui signale aussitôt l'appel à CallKit.
    return {"sheldon": _call_info(call, preview, push_key)}


def call_alert_payload(call: Dict[str, Any], preview: bool, *, push_key: bytes) -> Dict[str, Any]:
    """L'appel sur un appareil qui ne sonne pas (Mac, iPhone sans jeton PushKit) : « Hermes veut te parler »."""
    if preview:
        alert: Dict[str, Any] = {
            "title-loc-key": "PUSH_CALL_TITLE", "title-loc-args": [call["agentName"]], "body": plain_preview(call["reason"]),
        }
    else:
        alert = {"title-loc-key": "PUSH_CALL_TITLE_ANON", "loc-key": "PUSH_CALL_BODY_ANON"}
    return {
        "aps": {
            "alert": alert, "sound": "default", "category": "sheldon.call", "thread-id": "calls",
            "interruption-level": "time-sensitive",
        },
        "sheldon": _call_info(call, preview, push_key),
    }


class PushService:
    """Qui reçoit quoi. Sans client (APNs pas réglé), ne fait rien."""

    def __init__(
        self,
        store: DeviceStore,
        client: Optional[ApnsClient],
        preview: bool = True,
        spawn: Optional[Callable[[Awaitable[None]], Any]] = None,
    ) -> None:
        self._store = store
        self._client = client
        self._preview = preview
        self._spawn = spawn or self._spawn_task
        self._tasks: Set["asyncio.Task[None]"] = set()
        self._closed = False

    @classmethod
    def from_directory(cls, store: DeviceStore, directory: Path, run: Runner = run_command, curl: str = CURL) -> "PushService":
        config = load_config(directory)
        if config is None:
            return cls(store, None)
        if not _curl_supports_http2(curl):
            logger.warning("Sheldon: %s ne supporte pas HTTP/2 : notifications désactivées", curl)
            return cls(store, None)
        try:
            client = ApnsClient(config, ApnsToken(config), run=run, curl=curl)
        except (InsecureKeyError, OSError) as error:
            logger.warning("Sheldon: %s", error)
            return cls(store, None)
        return cls(store, client, preview=config.preview)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def request_changed(self, request: Request, sender: str, created: bool, badge: int) -> None:
        """badge : le nombre de demandes encore en attente."""
        if request.status in ("pending", "answering"):
            if created:
                payload = request_payload(request, sender, self._preview, badge, push_key=self._store.push_key())
                # Sans échéance (une proposition sans délai), aucun en-tête : Apple stocke
                # tant que l'appareil est hors ligne plutôt que de perdre la notification.
                expiration = int(request.expires_at) if request.expires_at is not None else None
                self._send_all(lambda target: Push(target.token, target.environment, payload, "alert", request.id, expiration))
        else:
            self._send_all(lambda target: Push(target.token, target.environment, closed_payload(request.id), "background"))

    def reply(self, conversation_id: str, sender: str, text: str, file_name: Optional[str] = None) -> None:
        payload = reply_payload(conversation_id, sender, text, self._preview, file_name, push_key=self._store.push_key())
        self._send_all(lambda target: Push(target.token, target.environment, payload))

    def task(self, item: Dict[str, Any]) -> None:
        payload = task_payload(item, self._preview, push_key=self._store.push_key())
        self._send_all(lambda target: Push(target.token, target.environment, payload, "alert", item["id"]))

    def device_paired(self, device_id: str, name: str) -> None:
        """Aux appareils déjà reliés seulement : le nouveau sait qu'il vient de se relier."""
        if self._client is None:
            return
        targets = [target for target in self._store.push_targets() if target.device_id != device_id]
        if targets:
            payload = device_payload(device_id, name, self._preview)
            self._launch(self._deliver([(target, Push(target.token, target.environment, payload)) for target in targets]))

    def call(self, call: Dict[str, Any], voip: List[PushTarget], alerts: List[PushTarget]) -> None:
        """call : Call.to_json() (core/calls.py) ; voip sonne, alerts reçoit une alerte. Les garde-fous sont déjà passés."""
        if self._client is None:
            return
        key = self._store.push_key()
        ringing = call_payload(call, self._preview, push_key=key)
        alerting = call_alert_payload(call, self._preview, push_key=key)
        pushes = [
            (target, Push(target.voip_token, target.environment, ringing, "voip", call["callId"]))
            for target in voip if target.voip_token
        ]
        pushes += [(target, Push(target.token, target.environment, alerting, "alert", call["callId"])) for target in alerts]
        if pushes:
            self._launch(self._deliver(pushes))

    def _send_all(self, make: Callable[[PushTarget], Push]) -> None:
        if self._client is None:
            return
        targets = self._store.push_targets()
        if targets:
            self._launch(self._deliver([(target, make(target)) for target in targets]))

    async def _deliver(self, pushes: List[Tuple[PushTarget, Push]]) -> None:
        assert self._client is not None
        for target, push in pushes:
            try:
                result = await self._client.send(push)
            except Exception:
                logger.exception("Sheldon: APNs push failed")
                continue
            if result.dead_token:
                if push.push_type == "voip":
                    self._store.clear_voip_token(target.device_id, push.token)
                else:
                    self._store.clear_push_token(target.device_id, target.token)
            elif result.status != 200:
                logger.warning("Sheldon: APNs refused a push (%s %s)", result.status, result.reason)

    def _launch(self, coroutine: Coroutine[Any, Any, None]) -> None:
        # Après aclose() (arrêt du gateway), plus rien ne part : un envoi lancé là resterait en
        # vol, curl compris (revue finale, M2).
        if self._closed:
            coroutine.close()
            return
        self._spawn(coroutine)

    def _spawn_task(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        """Annule les envois encore en vol (leur curl compris, tué par CancelledError dans
        run_command) et les attend, pour que l'arrêt du gateway ne laisse rien tourner."""
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
