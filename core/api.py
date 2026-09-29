"""La porte d'entrée de l'app : HTTP et WebSocket sous /v1.

Chaque appel porte la clé de son appareil (« Authorization: Bearer ... »),
sauf l'appairage, l'inscription du Mac (clé du compte) et /v1/health.
Les routes des étapes 2 à 5 (conversations, agents, demandes, fil, créations,
fichiers, notifications) lisent le runtime par le protocole Services.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple

from aiohttp import web

from .bridge import INPUT_MODES, SheldonBridge
from .calls import parse_quiet_hours
from .conversations import Conversation, ConversationCatalog
from .errors import SheldonError
from .events import Events
from .feed import FeedItem, FeedStore
from .files import FileStore
from .history import HistoryReader
from .hub import EventHub
from .media import plain_preview
from .pair_link import check_serve, qr_png_bytes
from .pairing import PairingError, PairingService
from .push import ENVIRONMENTS, normalize_device_token
from .requests import RequestService
from .store import Device, DeviceStore
from .timeutil import iso_utc
from .turn_context import ContextError, parse_context
from .version import API_VERSION, FEATURES, __version__

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 20_000
MAX_BODY_BYTES = 256 * 1024
DEFAULT_PAGE = 50
MAX_PAGE = 200
CLOSE_REVOKED = 4401
_CLIENT_MESSAGE_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
_PUBLIC = {("GET", "/v1/health"), ("POST", "/v1/pair"), ("POST", "/v1/devices/enroll")}
_PAIRING_STATUS = {"invalid_request": 400, "pair_code_invalid": 410, "owner_token_invalid": 401}
_ERROR_STATUS = {
    "invalid_request": 400,
    "conversation_protected": 400,
    "conversation_not_found": 404,
    "agent_not_found": 404,
    "request_not_found": 404,
    "feed_item_not_found": 404,
    "file_not_found": 404,
    "conversation_exists": 409,
    "request_closed": 409,
    "request_expired": 410,
    "hermes_unavailable": 503,
}
_FILE_ID = re.compile(r"[0-9a-f]{32}")
# Un type qu'un aperçu (WKWebView) exécuterait comme un document actif plutôt que de
# l'afficher passivement : jamais servi sans bac à sable, même en inline.
_SANDBOXED_MIME_TYPES = ("text/html", "image/svg+xml")
MAX_DECIDED = 100
DEFAULT_DECIDED = 20
LAST_MESSAGE_LENGTH = 200
_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed", 413: "body_too_large"}


class ApiError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class SocketRegistry:
    """Les WebSockets ouverts, rangés par appareil, pour les fermer au retrait."""

    def __init__(self) -> None:
        self._by_device: Dict[str, Set[web.WebSocketResponse]] = {}

    def add(self, device_id: str, ws: web.WebSocketResponse) -> None:
        self._by_device.setdefault(device_id, set()).add(ws)

    def remove(self, device_id: str, ws: web.WebSocketResponse) -> None:
        sockets = self._by_device.get(device_id)
        if sockets is not None:
            sockets.discard(ws)
            if not sockets:
                del self._by_device[device_id]

    async def close_device(self, device_id: str) -> None:
        for ws in list(self._by_device.get(device_id, ())):
            await close_revoked(ws)

    async def close_all(self) -> None:
        for sockets in list(self._by_device.values()):
            for ws in list(sockets):
                with contextlib.suppress(Exception):
                    await ws.close(code=1001, message=b"server_shutdown")


async def close_revoked(ws: web.WebSocketResponse) -> None:
    if ws.closed:
        return
    with contextlib.suppress(Exception):
        await ws.send_json({"type": "device.revoked"})
        await ws.close(code=CLOSE_REVOKED, message=b"device_revoked")


class Services(Protocol):
    """Ce que la porte d'entrée lit dans le runtime (core/runtime.py, classe Runtime)."""

    catalog: ConversationCatalog
    requests: RequestService
    feed: FeedStore
    files: FileStore
    events: Events

    def conversation(self, conversation_id: object) -> Optional[Conversation]: ...

    def history_for(self, conversation: Conversation) -> HistoryReader: ...

    def feed_detail(self, item: FeedItem) -> Tuple[List[Dict[str, Any]], Optional[str]]: ...


@dataclass
class ApiContext:
    store: DeviceStore
    pairing: PairingService
    bridge: SheldonBridge
    hub: EventHub
    status_provider: Callable[[], Dict[str, Any]]
    services: Services
    revalidate_seconds: float
    # Tâche 18 : les mêmes sondes et les mêmes ports que « hermes sheldon pair » et l'outil
    # sheldon_pair (core/pair_link.check_serve), pour que /v1/pair/offers refuse dans les mêmes
    # conditions (Serve absent, Funnel ouvert) sans dupliquer ce contrôle.
    find_tailscale: Callable[[], Optional[str]]
    probe_host: Callable[[str], Optional[str]]
    probe_serve: Callable[[str], Optional[str]]
    health_ok: Callable[[int], bool]
    public_port: int
    local_port: int
    is_enabled: Callable[[], bool]
    sockets: SocketRegistry = field(default_factory=SocketRegistry)
    pending: Dict[str, "asyncio.Future"] = field(default_factory=dict)
    # Relecture (m2) : une seule offre valant à la fois, un seul appel à /v1/pair/offers sonde
    # Tailscale à la fois (jusqu'à ~12 s au pire), au lieu d'en lancer un par appareil qui insiste.
    offer_lock: "asyncio.Lock" = field(default_factory=asyncio.Lock)


CTX = web.AppKey("sheldon_ctx", ApiContext)
# L'appareil qui appelle, posé par le middleware. Une clé typée : une clé texte déclenche
# NotAppKeyWarning sous aiohttp 3.14 (une fois par processus), donc un 500 sous -W error.
DEVICE = web.RequestKey("sheldon_device", Device)


def _error(status: int, code: str) -> web.Response:
    return web.json_response({"error": code}, status=status)


def _bearer(request: web.Request) -> Optional[str]:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    # Les clés (appareil ou compte) sont toujours de l'ASCII (URL-safe) : un jeton qui ne
    # l'est pas ne peut être qu'un octet mal formé (aiohttp décode l'en-tête en UTF-8 avec
    # surrogateescape), qui ferait planter hash_secret() en 500 plutôt qu'un 401 propre.
    if scheme != "Bearer" or not token or not token.isascii():
        return None
    return token


async def _read_json(request: web.Request) -> Dict[str, Any]:
    # Le corps est lu tel quel, sans se fier au charset annoncé par le client (qui peut être
    # invalide), et toute erreur de décodage (JSON malformé, imbrication trop profonde, entier
    # trop long) devient un 400 plutôt qu'un 500.
    try:
        raw = await request.read()
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise ApiError(400, "invalid_request")
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_request")
    try:
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        # Une chaîne avec un surrogate isolé (ex. "\ud800") se décode sans erreur mais ne
        # s'encode jamais en UTF-8 ; mieux vaut le refuser ici qu'au moment d'écrire en base.
        raise ApiError(400, "invalid_request")
    return data


def _int_param(request: web.Request, name: str, default: Optional[int], minimum: int, maximum: Optional[int]) -> Optional[int]:
    raw = request.query.get(name)
    if raw is None:
        return default
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 18):
        raise ApiError(400, "invalid_request")
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        raise ApiError(400, "invalid_request")
    return value


def _device_json(device: Device, current_id: str) -> Dict[str, Any]:
    return {
        "id": device.id,
        "name": device.name,
        "platform": device.platform,
        "createdAt": iso_utc(device.created_at),
        "lastSeenAt": iso_utc(device.last_seen_at) if device.last_seen_at is not None else None,
        "current": device.id == current_id,
    }


@web.middleware
async def _errors_and_auth(request: web.Request, handler):
    try:
        if (request.method, request.path) not in _PUBLIC:
            ctx = request.app[CTX]
            token = _bearer(request)
            device = ctx.store.device_for_token(token) if token else None
            if device is None:
                raise ApiError(401, "device_token_invalid")
            ctx.store.touch_device(device.id)
            request[DEVICE] = device
        return await handler(request)
    except ApiError as error:
        return _error(error.status, error.code)
    except PairingError as error:
        return _error(_PAIRING_STATUS.get(error.code, 400), error.code)
    except SheldonError as error:
        return _error(_ERROR_STATUS.get(error.code, 400), error.code)
    except sqlite3.Error:
        # La base est partagée avec « hermes sheldon » : verrouillée plus de 5 s, elle ne
        # répond pas. L'app réessaie plus tard, comme pour Hermes indisponible.
        logger.exception("Sheldon: device storage unavailable")
        return _error(503, "storage_unavailable")
    except web.HTTPException as error:
        # aiohttp répond lui-même aux routes inconnues, méthodes refusées ou corps trop gros
        # (ce sont des Response valides) mais en texte brut : on les remet au format JSON commun.
        if error.status < 400:
            raise
        code = _HTTP_ERROR_CODES.get(error.status, f"http_{error.status}")
        response = _error(error.status, code)
        if "Allow" in error.headers:
            response.headers["Allow"] = error.headers["Allow"]
        return response


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "apiVersion": API_VERSION})


def _announce_device(ctx: ApiContext, device: Device) -> None:
    """Les appareils déjà reliés apprennent qu'un nouvel appareil s'est relié (flux et notification).

    L'appareil vient de l'écriture même, jamais d'une relecture : il existe et le code est
    consommé, une relecture qui échouerait répondrait 503 et un nouvel essai 410.
    """
    ctx.services.events.device_paired(_device_json(device, ""))


async def _pair(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    body = await _read_json(request)
    result = ctx.pairing.redeem(body.get("code"), body.get("deviceName"), body.get("platform"))
    _announce_device(ctx, result.device)
    return web.json_response({
        "serverId": result.server_id,
        "ownerToken": result.owner_token,
        "deviceId": result.device_id,
        "deviceToken": result.device_token,
        "pushKey": result.push_key,
    })


async def _enroll(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    owner_token = _bearer(request)
    if not owner_token or not ctx.store.owner_token_matches(owner_token):
        raise ApiError(401, "owner_token_invalid")
    body = await _read_json(request)
    result = ctx.pairing.enroll(owner_token, body.get("deviceName"), body.get("platform"))
    _announce_device(ctx, result.device)
    return web.json_response({
        "serverId": result.server_id, "deviceId": result.device_id, "deviceToken": result.device_token,
        "pushKey": result.push_key,
    })


async def _offer_body(request: web.Request) -> None:
    """Corps vide ou objet JSON : /v1/pair/offers ne lit rien dedans, mais un corps malformé
    reste un 400 (comme _read_json, sans exiger un objet quand le corps est vide)."""
    raw = await request.read()
    if not raw:
        return
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise ApiError(400, "invalid_request")
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_request")


async def _create_pair_offer(request: web.Request) -> web.Response:
    """Relier un autre appareil depuis un appareil déjà relié (tâche 18, ajout A56) : la même
    offre, les mêmes plafonds et le même contrôle Serve/Funnel que l'outil sheldon_pair et la
    commande (core/pair_link.check_serve, core/pairing.PairingService.start(by_tool=True))."""
    ctx = request.app[CTX]
    # Refusé avant toute question à Tailscale, comme l'outil : jamais paths.enable() ici,
    # « hermes sheldon disable » tient quoi que décide l'appareil qui appelle.
    if not ctx.is_enabled():
        raise ApiError(403, "disabled")
    await _offer_body(request)
    async with ctx.offer_lock:
        # Relecture (m2) : le plafond est contrôlé avant de sonder Tailscale (jusqu'à environ
        # 12 s au pire), pour qu'une requête au-delà des 5 par heure n'en paie jamais le prix ;
        # start() garde le contrôle qui compte, atomique, sous son propre verrou de la base.
        if ctx.pairing.offer_limit_reached():
            raise ApiError(429, "too_many_offers")
        check = await asyncio.to_thread(
            check_serve, find_tailscale=ctx.find_tailscale, probe_host=ctx.probe_host, probe_serve=ctx.probe_serve,
            health_ok=ctx.health_ok, public_port=ctx.public_port, local_port=ctx.local_port,
        )
        if check.host is None:
            # reason (relecture m1) : « serve_missing » (pas prêt) ou « funnel_open » (alarme,
            # Sheldon serait exposé à Internet) ; l'app localise elle-même.
            return web.json_response({"error": "serve_not_ready", "reason": check.reason}, status=409)
        try:
            offer = ctx.pairing.start(check.host, ctx.public_port, by_tool=True)
        except PairingError:
            raise ApiError(429, "too_many_offers")
    device: Device = request[DEVICE]
    # Jamais le code ni le lien dans le journal (menace de la ronde de sécurité de la tâche 16) :
    # seul l'identifiant de l'appareil qui a demandé l'offre.
    logger.info("Sheldon: pairing offer created by device %s", device.id)
    return web.json_response({
        "link": offer.link,
        "expiresAt": iso_utc(offer.expires_at),
        "qrPng": base64.b64encode(qr_png_bytes(offer.link)).decode("ascii"),
    }, status=201)


async def _status(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    return web.json_response({
        "apiVersion": API_VERSION,
        "serverId": ctx.store.server_id(),
        "extensionVersion": __version__,
        **ctx.status_provider(),
        "deviceId": request[DEVICE].id,
        "features": list(FEATURES),
    })


def _conversation(request: web.Request) -> Conversation:
    # Une conversation du catalogue, ou celle d'une carte du fil (« feed-<id> »).
    conversation = request.app[CTX].services.conversation(request.match_info["conversation_id"])
    if conversation is None:
        raise ApiError(404, "conversation_not_found")
    return conversation


def _last_message(ctx: "ApiContext", conversation: Conversation) -> Optional[Dict[str, Any]]:
    """Le dernier message de l'historique, ou la dernière carte du fil si elle est plus récente."""
    try:
        page = ctx.services.history_for(conversation).page(None, 1)
    except Exception:
        logger.warning("Sheldon: last message of %s unavailable", conversation.id, exc_info=True)
        page = None
    last = None
    if page is not None and page.messages:
        message = page.messages[-1]
        # Lisible comme le corps des pushes : sans blocs visuels ni Markdown (revue finale, M11).
        last = {"role": message["role"], "text": plain_preview(message["text"], LAST_MESSAGE_LENGTH), "createdAt": message["createdAt"]}
    item = ctx.services.feed.latest(conversation.id)
    if item is not None and (last is None or iso_utc(item.created_at) > last["createdAt"]):
        last = {"role": "assistant", "text": item.summary or item.title, "createdAt": iso_utc(item.created_at)}
    return last


async def _list_conversations(request: web.Request) -> web.Response:
    ctx = request.app[CTX]

    def collect() -> List[Dict[str, Any]]:
        return [c.to_json(_last_message(ctx, c)) for c in ctx.services.catalog.list()]

    return web.json_response({"conversations": await asyncio.to_thread(collect)})


async def _create_conversation(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    body = await _read_json(request)
    conversation = ctx.services.catalog.add_topic(body.get("title"), body.get("agentId"))
    ctx.services.events.conversation_changed(conversation)
    return web.json_response({"conversation": conversation.to_json(None)}, status=201)


async def _delete_conversation(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    conversation_id = request.match_info["conversation_id"]
    ctx.services.catalog.remove_topic(conversation_id)
    ctx.services.events.conversation_deleted(conversation_id)
    return web.Response(status=204)


async def _list_agents(request: web.Request) -> web.Response:
    catalog = request.app[CTX].services.catalog
    agents = [agent.to_json(catalog.conversation_id_for(agent)) for agent in catalog.agents()]
    return web.json_response({"agents": agents, "multiplex": catalog.multiplex()})


async def _list_messages(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    conversation = _conversation(request)
    limit = _int_param(request, "limit", DEFAULT_PAGE, 1, MAX_PAGE)
    before = _int_param(request, "before", None, 1, None)
    try:
        page = await asyncio.to_thread(ctx.services.history_for(conversation).page, before, limit)
    except Exception:
        logger.exception("Sheldon: history read failed")
        raise ApiError(503, "history_unavailable")
    return web.json_response({"messages": page.messages, "nextBefore": page.next_before})


async def _send_message(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    device: Device = request[DEVICE]
    conversation = _conversation(request)
    body = await _read_json(request)
    client_message_id = body.get("clientMessageId")
    text = body.get("text")
    input_mode = body.get("inputMode", "text")
    if not isinstance(client_message_id, str) or not _CLIENT_MESSAGE_ID.fullmatch(client_message_id):
        raise ApiError(400, "invalid_request")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
        raise ApiError(400, "invalid_request")
    if input_mode not in INPUT_MODES:
        raise ApiError(400, "invalid_request")
    try:
        # Le contexte d'un tour dit à voix haute (plan 6, tâche 1) : vérifié avant de retenir le message.
        context = parse_context(body.get("context"))
    except ContextError:
        raise ApiError(400, "invalid_request")
    if not ctx.store.remember_client_message(client_message_id, device.id):
        # Un renvoi peut arriver pendant que la première soumission est encore en cours : on
        # attend son issue plutôt que de répondre au hasard avant de la connaître, sans quoi
        # l'app pourrait croire le message livré alors qu'il n'est jamais arrivé (ou l'inverse).
        pending = ctx.pending.get(client_message_id)
        if pending is not None and not await pending:
            raise ApiError(503, "hermes_unavailable")
        return web.json_response({"accepted": True, "duplicate": True})
    pending = ctx.pending[client_message_id] = asyncio.get_running_loop().create_future()
    try:
        await ctx.bridge.submit_user_message(text, client_message_id, conversation.id, input_mode, context)
    except Exception:
        logger.exception("Sheldon: could not hand the message to Hermes")
        ctx.store.forget_client_message(client_message_id)
        pending.set_result(False)
        raise ApiError(503, "hermes_unavailable")
    else:
        pending.set_result(True)
        return web.json_response({"accepted": True, "duplicate": False}, status=202)
    finally:
        del ctx.pending[client_message_id]


async def _list_devices(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    current = request[DEVICE].id
    return web.json_response({"devices": [_device_json(d, current) for d in ctx.store.list_devices()]})


async def _revoke_device(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    device_id = request.match_info["device_id"]
    if not ctx.store.revoke_device(device_id):
        raise ApiError(404, "device_not_found")
    await ctx.sockets.close_device(device_id)
    return web.Response(status=204)


async def _set_push_token(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    device: Device = request[DEVICE]
    body = await _read_json(request)
    token = normalize_device_token(body.get("token"))
    environment = body.get("environment")
    if token is None or environment not in ENVIRONMENTS:
        raise ApiError(400, "invalid_request")
    # Les réglages des appels (tâche 17). La route remplace tout : un champ absent vaut « aucun »
    # (pas de jeton PushKit, pas d'heures calmes) et callsAllowed vaut true.
    raw_voip = body.get("voipToken")
    voip_token = normalize_device_token(raw_voip) if raw_voip not in (None, "") else None
    calls_allowed = body.get("callsAllowed", True)
    if (raw_voip not in (None, "") and voip_token is None) or not isinstance(calls_allowed, bool):
        raise ApiError(400, "invalid_request")
    try:
        quiet_hours = parse_quiet_hours(body.get("quietHours"))
    except ValueError:
        raise ApiError(400, "invalid_request") from None
    ctx.store.set_push_token(
        device.id, token, environment, voip_token=voip_token, calls_allowed=calls_allowed, quiet_hours=quiet_hours,
    )
    return web.Response(status=204)


async def _clear_push_token(request: web.Request) -> web.Response:
    request.app[CTX].store.clear_push_token(request[DEVICE].id)
    return web.Response(status=204)


async def _list_requests(request: web.Request) -> web.Response:
    requests = request.app[CTX].services.requests
    limit = _int_param(request, "decidedLimit", DEFAULT_DECIDED, 1, MAX_DECIDED)
    return web.json_response({
        "pending": [r.to_json() for r in requests.pending()],
        "decided": [r.to_json() for r in requests.decided(limit)],
    })


async def _get_request(request: web.Request) -> web.Response:
    found = request.app[CTX].services.requests.get(request.match_info["request_id"])
    if found is None:
        raise ApiError(404, "request_not_found")
    return web.json_response({"request": found.to_json()})


async def _answer_request(request: web.Request) -> web.Response:
    body = await _read_json(request)
    answered = await request.app[CTX].services.requests.answer(
        request.match_info["request_id"], choice_id=body.get("choiceId"), text=body.get("text")
    )
    return web.json_response({"request": answered.to_json()})


async def _list_feed(request: web.Request) -> web.Response:
    limit = _int_param(request, "limit", DEFAULT_PAGE, 1, MAX_PAGE)
    before = _int_param(request, "before", None, 1, None)
    items, next_before = request.app[CTX].services.feed.page(before, limit)
    return web.json_response({"items": [i.to_json() for i in items], "nextBefore": next_before})


async def _feed_item(request: web.Request) -> web.Response:
    services = request.app[CTX].services
    item = services.feed.get(request.match_info["item_id"])
    if item is None:
        raise ApiError(404, "feed_item_not_found")
    try:
        steps, reasoning = await asyncio.to_thread(services.feed_detail, item)
    except Exception:
        # La session de la tâche est illisible (base verrouillée) : la page Voir montre le texte seul.
        logger.warning("Sheldon: steps of %s unavailable", item.id, exc_info=True)
        steps, reasoning = [], None
    return web.json_response({"item": item.to_json(), "text": item.text, "steps": steps, "reasoning": reasoning})


async def _list_creations(request: web.Request) -> web.Response:
    limit = _int_param(request, "limit", DEFAULT_PAGE, 1, MAX_PAGE)
    before = _int_param(request, "before", None, 1, None)
    files, next_before = request.app[CTX].services.files.creations(before, limit)
    return web.json_response({"creations": [f.creation_json() for f in files], "nextBefore": next_before})


async def _get_file(request: web.Request) -> web.StreamResponse:
    files = request.app[CTX].services.files
    file_id = request.match_info["file_id"]
    stored = files.get(file_id) if _FILE_ID.fullmatch(file_id) else None
    path = files.path_of(stored) if stored is not None else None
    if path is None or not path.is_file():
        raise ApiError(404, "file_not_found")
    headers = {
        "Content-Type": stored.mime_type,
        "Content-Disposition": f"inline; filename*=UTF-8''{urllib.parse.quote(stored.name)}",
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    if stored.mime_type in _SANDBOXED_MIME_TYPES:
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return web.FileResponse(path, headers=headers)


async def _pump(ws: web.WebSocketResponse, queue: asyncio.Queue) -> None:
    try:
        while not ws.closed:
            await ws.send_json(await queue.get())
    except (ConnectionResetError, RuntimeError):
        return


async def _watch(ws: web.WebSocketResponse, ctx: ApiContext, device_id: str) -> None:
    # Un retrait fait depuis le terminal (autre processus) se voit ici.
    while not ws.closed:
        await asyncio.sleep(ctx.revalidate_seconds)
        try:
            still_known = ctx.store.device_by_id(device_id) is not None
        except Exception:
            # Une erreur passagère de la base ne doit pas laisser cette tâche mourir en
            # silence avec un socket ouvert et muet : on ferme, l'app se reconnectera.
            logger.exception("Sheldon: could not check whether the device is still known")
            with contextlib.suppress(Exception):
                await ws.close(code=1011, message=b"internal_error")
            return
        if not still_known:
            await close_revoked(ws)
            return


async def _drain(ws: web.WebSocketResponse) -> None:
    async for _message in ws:
        pass  # l'app n'envoie rien sur ce canal en v1


def _log_task_failure(task: "asyncio.Task[None]") -> None:
    # Un rappel plutôt qu'une lecture après le gather : il passe même si le handler est annulé.
    if not task.cancelled() and task.exception() is not None:
        logger.error("Sheldon: an events task failed", exc_info=task.exception())


async def _events(request: web.Request) -> web.WebSocketResponse:
    ctx = request.app[CTX]
    device: Device = request[DEVICE]
    ws = web.WebSocketResponse(heartbeat=20.0)
    await ws.prepare(request)
    queue = ctx.hub.subscribe()
    ctx.sockets.add(device.id, ws)
    try:
        still_known = ctx.store.device_by_id(device.id) is not None
    except Exception:
        # Une erreur passagère ici ne doit pas faire échouer toute la connexion : _watch
        # revérifiera bientôt, dans sa propre boucle protégée.
        logger.exception("Sheldon: could not check whether the device is still known")
        still_known = True
    if not still_known:
        # Retiré entre le contrôle de la clé et l'inscription du socket : on ne rate pas ce
        # retrait-là juste parce qu'on vient d'arriver.
        await close_revoked(ws)
        ctx.hub.unsubscribe(queue)
        ctx.sockets.remove(device.id, ws)
        return ws
    tasks = []
    try:
        await ws.send_json({"type": "hello", "apiVersion": API_VERSION, "serverId": ctx.store.server_id()})
        tasks = [
            asyncio.create_task(_pump(ws, queue)),
            asyncio.create_task(_watch(ws, ctx, device.id)),
            asyncio.create_task(_drain(ws)),
        ]
        for task in tasks:
            task.add_done_callback(_log_task_failure)
        # Si l'une des trois tâches s'arrête (connexion coupée, erreur), on ne laisse pas le
        # socket ouvert et muet en attendant que le client s'en aperçoive de son côté.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close(code=1011, message=b"internal_error")
    finally:
        # D'abord ce qui ne demande aucune attente : le handler peut être annulé pendant qu'il
        # attend ses tâches (fermeture de la connexion, arrêt du serveur).
        ctx.hub.unsubscribe(queue)
        ctx.sockets.remove(device.id, ws)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return ws


async def _close_sockets(app: web.Application) -> None:
    await app[CTX].sockets.close_all()


def create_app(
    *,
    store: DeviceStore,
    pairing: PairingService,
    bridge: SheldonBridge,
    hub: EventHub,
    status_provider: Callable[[], Dict[str, Any]],
    services: Services,
    find_tailscale: Callable[[], Optional[str]],
    probe_host: Callable[[str], Optional[str]],
    probe_serve: Callable[[str], Optional[str]],
    health_ok: Callable[[int], bool],
    public_port: int,
    local_port: int,
    is_enabled: Callable[[], bool],
    revalidate_seconds: float = 5.0,
) -> web.Application:
    app = web.Application(middlewares=[_errors_and_auth], client_max_size=MAX_BODY_BYTES)
    app[CTX] = ApiContext(
        store=store, pairing=pairing, bridge=bridge, hub=hub, status_provider=status_provider,
        services=services, revalidate_seconds=revalidate_seconds, find_tailscale=find_tailscale,
        probe_host=probe_host, probe_serve=probe_serve, health_ok=health_ok, public_port=public_port,
        local_port=local_port, is_enabled=is_enabled,
    )
    app.router.add_get("/v1/health", _health)
    app.router.add_post("/v1/pair", _pair)
    app.router.add_post("/v1/pair/offers", _create_pair_offer)
    app.router.add_post("/v1/devices/enroll", _enroll)
    app.router.add_get("/v1/status", _status)
    app.router.add_get("/v1/conversations", _list_conversations)
    app.router.add_post("/v1/conversations", _create_conversation)
    app.router.add_delete("/v1/conversations/{conversation_id}", _delete_conversation)
    app.router.add_get("/v1/conversations/{conversation_id}/messages", _list_messages)
    app.router.add_post("/v1/conversations/{conversation_id}/messages", _send_message)
    app.router.add_get("/v1/agents", _list_agents)
    app.router.add_get("/v1/requests", _list_requests)
    app.router.add_get("/v1/requests/{request_id}", _get_request)
    app.router.add_post("/v1/requests/{request_id}/answer", _answer_request)
    app.router.add_get("/v1/feed", _list_feed)
    app.router.add_get("/v1/feed/{item_id}", _feed_item)
    app.router.add_get("/v1/creations", _list_creations)
    app.router.add_get("/v1/files/{file_id}", _get_file)
    app.router.add_get("/v1/devices", _list_devices)
    app.router.add_put("/v1/devices/current/push", _set_push_token)
    app.router.add_delete("/v1/devices/current/push", _clear_push_token)
    app.router.add_delete("/v1/devices/{device_id}", _revoke_device)
    app.router.add_get("/v1/events", _events)
    app.on_shutdown.append(_close_sockets)
    return app
