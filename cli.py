"""hermes sheldon pair [--after-restart] | devices | revoke <id> | reset --yes | disable | push ... | chats ..."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .core import paths, tailscale, welcome
from .core.conversations import MAX_TITLE_LENGTH, ConversationCatalog, ConversationError
from .core.messages import language, t
from .core.outbox import Outbox
from .core.pair_link import check_serve, extension_listening
from .core.pairing import PAIR_CODE_TTL_SECONDS, PairingService
from .core.push import (
    DEFAULT_TOPIC, ApnsClient, ApnsToken, InsecureKeyError, Push, PushSetupError, Runner, load_config, run_command, save_config,
)
from .core.store import DeviceStore
from .core.timeutil import iso_utc


def _show_qr(link: str) -> None:
    import qrcode

    code = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(link)
    code.make(fit=True)
    code.print_ascii(out=sys.stdout, invert=True)


def run_pair(
    *,
    store: DeviceStore,
    find_tailscale: Callable[[], Optional[str]],
    probe_host: Callable[[str], Optional[str]],
    probe_serve: Callable[[str], Optional[str]],
    health_ok: Callable[[int], bool],
    public_port: int,
    local_port: int,
    out: Callable[[str], Any],
    show_qr: Callable[[str], Any],
) -> int:
    # Les mêmes contrôles que l'outil sheldon_pair (core/pair_link.py).
    check = check_serve(
        find_tailscale=find_tailscale, probe_host=probe_host, probe_serve=probe_serve,
        health_ok=health_ok, public_port=public_port, local_port=local_port,
    )
    if check.host is None:
        for line in check.problem:
            out(line)
        return 1
    offer = PairingService(store).start(check.host, public_port)
    out(t("scan"))
    show_qr(offer.link)
    out(t("link"))
    out("  " + offer.link)
    out(t("expires", minutes=PAIR_CODE_TTL_SECONDS // 60))
    out(t("each_device"))
    out(t("key_expiry"))
    return 0


def _open_store() -> DeviceStore:
    return DeviceStore(paths.db_path())


def _pair() -> int:
    # Seul Léo, dans un vrai terminal et hors du gateway (par SSH), reçoit un code par la
    # commande. L'outil terminal d'Hermes peut donner un pseudo-terminal (pty=true), où isatty
    # vaut vrai ; mais le gateway pose _HERMES_GATEWAY=1 (gateway/run.py), que tous ses
    # descendants héritent (tâche 16, rondes 1 et 2).
    if os.environ.get("_HERMES_GATEWAY") == "1" or not (os.isatty(0) and os.isatty(1)):
        print(t("pair_needs_terminal"))
        return 1
    paths.enable()
    store = _open_store()
    try:
        return run_pair(
            store=store,
            find_tailscale=tailscale.find_binary,
            probe_host=tailscale.probe_host,
            probe_serve=tailscale.probe_serve_status,
            health_ok=extension_listening,
            public_port=paths.public_port(),
            local_port=paths.local_port(),
            out=print,
            show_qr=_show_qr,
        )
    finally:
        store.close()


def _pair_after_restart(lang: Optional[str]) -> int:
    """Pendant une installation demandée dans un chat : note ce chat, où l'extension enverra
    elle-même le QR code au redémarrage (core/welcome.py). Lancée par Hermes dans son terminal,
    sous le gateway : ni code, ni image, et jamais paths.enable()."""
    store = _open_store()
    try:
        return welcome.run_note(
            os.environ, enabled=paths.is_enabled(), store=store, path=paths.welcome_note(),
            language=lang or language(), now=time.time(), out=print,
        )
    finally:
        store.close()


def _devices() -> int:
    store = _open_store()
    try:
        devices = store.list_devices()
        if not devices:
            print(t("devices_empty"))
        for device in devices:
            seen = iso_utc(device.last_seen_at) if device.last_seen_at is not None else t("never")
            print(t("device_line", name=device.name, platform=device.platform, id=device.id, created=iso_utc(device.created_at), seen=seen))
        return 0
    finally:
        store.close()


def _revoke(device_id: str) -> int:
    store = _open_store()
    try:
        if not store.revoke_device(device_id):
            print(t("revoke_unknown", id=device_id))
            return 1
        print(t("revoked"))
        return 0
    finally:
        store.close()


def _reset(confirmed: bool) -> int:
    if not confirmed:
        print(t("reset_confirm"))
        return 1
    store = _open_store()
    try:
        store.reset_all()
    finally:
        store.close()
    print(t("reset_done"))
    return 0


def _disable() -> int:
    paths.disable()
    print(t("disabled"))
    return 0


def run_push_setup(directory: Path, key: str, key_id: str, team_id: str, topic: str, preview: bool, out: Callable[[str], Any]) -> int:
    try:
        config = save_config(directory, Path(key).expanduser(), key_id, team_id, topic, preview)
    except PushSetupError as error:
        out(t("push_setup_" + error.code))
        return 1
    except OSError:
        # La clé et ses réglages n'ont pas pu être écrits (dossier des notifications) : jamais
        # le texte de l'exception, en anglais et avec un chemin.
        out(t("push_setup_write_failed"))
        return 1
    out(t("push_setup_done", key_id=config.key_id, topic=config.topic))
    return 0


def run_push_status(directory: Path, store: DeviceStore, out: Callable[[str], Any]) -> int:
    config = load_config(directory)
    if config is None:
        out(t("push_off"))
        return 1
    try:
        ApnsToken(config)
    except InsecureKeyError:
        # Ce que verrait le gateway au démarrage (PushService.from_directory) : la clé ou son
        # dossier ne passent plus le contrôle de sécurité, les notifications sont coupées même
        # si le réglage existe toujours (constat Minor, relecture du lot 12-13).
        out(t("push_cut_off"))
        return 1
    out(t("push_on", key_id=config.key_id, team_id=config.team_id, topic=config.topic, preview=t("yes") if config.preview else t("no")))
    out(t("push_devices", count=len(store.push_targets())))
    return 0


def run_push_test(directory: Path, store: DeviceStore, out: Callable[[str], Any], run: Runner = run_command) -> int:
    config = load_config(directory)
    if config is None:
        out(t("push_off"))
        return 1
    targets = store.push_targets()
    if not targets:
        out(t("push_no_device"))
        return 1
    names = {device.id: device.name for device in store.list_devices()}
    try:
        client = ApnsClient(config, ApnsToken(config), run=run)
    except InsecureKeyError:
        out(t("push_test_key_insecure"))
        return 1
    payload = {"aps": {"alert": {"title": t("push_test_title"), "body": t("push_test_body")}, "sound": "default"}, "sheldon": {"type": "test"}}

    async def send_all() -> list:
        return [await client.send(Push(target.token, target.environment, payload, collapse_id="sheldon-test")) for target in targets]

    try:
        results = asyncio.run(send_all())
    except FileNotFoundError:
        # curl absent (ou son chemin invalide) : create_subprocess_exec lève ceci plutôt
        # qu'une trace Python peu claire (constat Minor, relecture du lot 12-13).
        out(t("push_test_curl_missing"))
        return 1
    for target, result in zip(targets, results):
        device = names.get(target.device_id, target.device_id)
        if result.status == 200:
            out(t("push_result_ok", device=device))
        else:
            # Le motif d'Apple (BadDeviceToken...) reste tel quel : c'est lui qu'on cherche dans
            # la documentation d'APNs.
            out(t("push_result_failed", device=device, status=result.status, reason=result.reason or t("unknown")))
    return 0 if all(result.status == 200 for result in results) else 1


# Une phrase par refus (messages.py), jamais le code brut de ConversationError.
CHAT_REFUSALS = {
    "invalid_request": "chat_title_invalid",
    "agent_not_found": "chat_agent_not_found",
    "conversation_exists": "chat_exists",
    "conversation_not_found": "chat_not_found",
    "conversation_protected": "chat_protected",
}


def run_chats(catalog: ConversationCatalog, outbox: Outbox, args: Any, out: Callable[[str], Any]) -> int:
    action = getattr(args, "chats_command", None) or "list"
    try:
        if action == "add":
            conversation = catalog.add_topic(args.title, getattr(args, "agent", None))
            outbox.append("conversation.upsert", {"conversationId": conversation.id})
            out(t("chat_added", id=conversation.id))
            return 0
        if action == "remove":
            catalog.remove_topic(args.conversation_id)
            outbox.append("conversation.deleted", {"conversationId": args.conversation_id})
            out(t("chat_removed"))
            return 0
    except ConversationError as error:
        out(t(CHAT_REFUSALS.get(error.code, "chat_refused"), max=MAX_TITLE_LENGTH))
        return 1
    for conversation in catalog.list():
        kind = t("kind_" + conversation.kind)
        out(t("chat_line", id=conversation.id, title=conversation.title, kind=kind, agent=conversation.agent_id))
    return 0


def _push(args: Any) -> int:
    action = getattr(args, "push_command", None) or "status"
    if action == "setup":
        return run_push_setup(paths.apns_dir(), args.key, args.key_id, args.team_id, args.topic, not args.no_preview, print)
    store = _open_store()
    try:
        if action == "test":
            return run_push_test(paths.apns_dir(), store, print)
        return run_push_status(paths.apns_dir(), store, print)
    finally:
        store.close()


def _chats(args: Any) -> int:
    from .adapter import HermesLink

    link = HermesLink()
    catalog = ConversationCatalog(paths.db_path(), link.agents, link.multiplex)
    outbox = Outbox(paths.db_path())
    try:
        return run_chats(catalog, outbox, args, print)
    finally:
        catalog.close()
        outbox.close()


def setup_parser(parser: Any) -> None:
    commands = parser.add_subparsers(dest="sheldon_command")
    pair = commands.add_parser("pair", help=t("help_pair"))
    pair.add_argument("--after-restart", action="store_true", help=t("help_pair_after_restart"))
    pair.add_argument("--lang", choices=["fr", "en"], help=t("help_lang"))
    commands.add_parser("devices", help=t("help_devices"))
    revoke = commands.add_parser("revoke", help=t("help_revoke"))
    revoke.add_argument("device_id")
    reset = commands.add_parser("reset", help=t("help_reset"))
    reset.add_argument("--yes", action="store_true")
    commands.add_parser("disable", help=t("help_disable"))
    push = commands.add_parser("push", help=t("help_push"))
    push_commands = push.add_subparsers(dest="push_command")
    setup = push_commands.add_parser("setup", help=t("help_push_setup"))
    setup.add_argument("--key", required=True)
    setup.add_argument("--key-id", required=True)
    setup.add_argument("--team-id", required=True)
    setup.add_argument("--topic", default=DEFAULT_TOPIC)
    setup.add_argument("--no-preview", action="store_true")
    push_commands.add_parser("status", help=t("help_push_status"))
    push_commands.add_parser("test", help=t("help_push_test"))
    chats = commands.add_parser("chats", help=t("help_chats"))
    chat_commands = chats.add_subparsers(dest="chats_command")
    chat_commands.add_parser("list", help=t("help_chats_list"))
    add = chat_commands.add_parser("add", help=t("help_chats_add"))
    add.add_argument("title")
    add.add_argument("--agent")
    remove = chat_commands.add_parser("remove", help=t("help_chats_remove"))
    remove.add_argument("conversation_id")


def handle(args: Any) -> int:
    command = getattr(args, "sheldon_command", None) or "pair"
    if command == "devices":
        return _devices()
    if command == "revoke":
        return _revoke(args.device_id)
    if command == "reset":
        return _reset(bool(getattr(args, "yes", False)))
    if command == "disable":
        return _disable()
    if command == "push":
        return _push(args)
    if command == "chats":
        return _chats(args)
    if getattr(args, "after_restart", False):
        return _pair_after_restart(getattr(args, "lang", None))
    return _pair()


def interactive_setup() -> None:
    _pair()
