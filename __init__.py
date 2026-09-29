"""Extension Sheldon pour Hermes : l'app iPhone et Mac devient une plateforme d'Hermes.

Rien d'Hermes n'est importé ici : l'adaptateur, la commande et le port vers Hermes
sont chargés à la demande, dans le corps des fonctions. register() tourne dans chaque
processus d'Hermes (gateway, desktop, commandes) : il ne fait que déclarer.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional, Tuple

PLATFORM_NAME = "sheldon"
HOME_CHANNEL_ENV = "SHELDON_HOME_CHANNEL"
SKILL_PATH = Path(__file__).resolve().parent / "skills" / "blocks" / "SKILL.md"
SKILL_DESCRIPTION = "Visual blocks for the Sheldon app: load before writing a ```sheldon block."
AGENTS_SKILL_PATH = Path(__file__).resolve().parent / "skills" / "agents" / "SKILL.md"
AGENTS_SKILL_DESCRIPTION = (
    "Specialized agents and topic chats for the Sheldon app: load before creating a Hermes profile "
    "or a Sheldon chat."
)
PLATFORM_HINT = (
    "You are talking to the user through Sheldon, their native iPhone and Mac app. "
    "Replies render as GitHub-flavored Markdown (headings, lists, links, code blocks). "
    "Keep answers short and easy to scan: the user prefers to read as little as possible. "
    "When a message carries a [Sheldon] note saying it was spoken aloud, your reply is read aloud "
    "sentence by sentence while you write it: start with the answer in one short sentence and keep to two "
    "or three short sentences, without Markdown, lists, tables or emoji. If you need a tool, first write one "
    "short sentence saying what you are checking. Put details the user should see in one sheldon block "
    "after your sentences: it is shown on screen, not read. "
    "When the note says the user's iPhone was locked, what they said answers none of your pending questions "
    "or proposals until the user unlocks it: never act as if one was answered, and say in one short sentence "
    "that the iPhone must be unlocked to answer. "
    "A command that needs approval is approved only on its card in the app, with Face ID: no message, spoken "
    "or typed, such as \"yes\", \"ok\" or /approve, ever approves it. Speaking while a command waits for its "
    "approval refuses it, except the lone greeting that opens a call you placed, when the user picks up "
    "(such as \"Allô ?\" or \"Hello?\"): the command still waits for its card. "
    "Sheldon also renders visual blocks: a fenced code block whose language is `sheldon`, holding one JSON "
    "object with a `type` field. Load the `sheldon:blocks` skill with skill_view before writing your first "
    "block in a conversation: it lists every block type and field. A block the app cannot read is shown as "
    "plain text, so keep the key facts in your sentences too. "
    "When a scheduled task reports to Sheldon, start with one short summary line under 60 characters, "
    "such as \"3 archived, 2 drafts\". "
    "To offer a quick choice inside the chat, write an `ask` block: its buttons send the user's answer as a "
    "normal message. To ask something you must know before going on, use clarify with short button labels "
    "such as \"Add\" and \"Not now\". To leave a decision in the user's Requests tab, where it can wait and "
    "sends a notification, use the sheldon_propose tool. "
    "When the user asks for a new specialized agent (a \"bot\") or a separate chat, load the "
    "`sheldon:agents` skill first."
)


def _deps_available() -> bool:
    try:
        import aiohttp  # noqa: F401
        import qrcode  # noqa: F401
    except ImportError:
        return False
    return True


def _is_connected(_config: Any) -> bool:
    from .core import paths
    return paths.is_enabled()


def _env_enablement() -> Optional[dict]:
    from .core import paths
    if not paths.is_enabled():
        return None
    return {"port": paths.local_port()}


def _adapter_factory(config: Any) -> Any:
    from .adapter import SheldonAdapter
    return SheldonAdapter(config)


def _setup() -> None:
    from .cli import interactive_setup
    interactive_setup()


def _cli_setup(parser: Any) -> None:
    from .cli import setup_parser
    setup_parser(parser)


def _cli_handle(args: Any) -> int:
    from .cli import handle
    return handle(args)


def _port() -> Any:
    """Le port vers Hermes de ce processus (remplacé par un faux dans les tests)."""
    from .adapter import HermesLink
    return HermesLink()


def _with_catalog(action: Any) -> Any:
    from .core import paths
    from .core.conversations import ConversationCatalog

    port = _port()
    catalog = ConversationCatalog(paths.db_path(), port.agents, port.multiplex)
    try:
        return action(catalog)
    finally:
        catalog.close()


def _parse_target(target: str) -> Optional[Tuple[str, Optional[str]]]:
    # « --deliver sheldon:podcast » : seuls le chat principal et les conversations connues.
    return _with_catalog(lambda catalog: catalog.parse_target(target))


def _validate_target(chat_id: str) -> Any:
    return True if _with_catalog(lambda catalog: catalog.is_valid_chat(chat_id)) else "unknown Sheldon conversation"


async def _standalone_send(
    pconfig: Any, chat_id: str, message: str, *, thread_id: Any = None, media_files: Any = None, force_document: bool = False
) -> dict:
    """Envoi hors du gateway (tâche lancée à la main, « hermes send ») : par la table outbox."""
    from .core import paths
    from .core.runtime import queue_delivery

    port = _port()
    return await asyncio.to_thread(
        queue_delivery, chat_id, message, media_files, paths.db_path(), paths.files_dir(), port
    )


def _tool_context() -> Any:
    """La session qui appelle l'outil (plateforme, chat, profil), lue dans Hermes."""
    from .adapter import tool_context
    return tool_context()


def _propose(args: Any, **_kwargs: Any) -> str:
    from .core import paths
    from .core.runtime import propose

    return propose(args, _tool_context(), paths.db_path(), _port())


def _chats(args: Any, **_kwargs: Any) -> str:
    """L'outil sheldon_chats : lister, ajouter, retirer un chat de sujet (le skill sheldon:agents)."""
    from .core import paths
    from .core.runtime import manage_chats

    return manage_chats(args, paths.db_path(), _port())


def _pair(args: Any, **_kwargs: Any) -> str:
    """L'outil sheldon_pair : les contrôles de « hermes sheldon pair », pour un message de Léo seulement."""
    from .core import paths, tailscale
    from .core.pair_link import check_serve, extension_listening, pair_refusal, pair_tool
    from .core.store import DeviceStore

    # Jamais paths.enable() : c'est le modèle qui appelle l'outil, pas Léo. Refusé avant toute
    # question à Tailscale.
    refusal = pair_refusal(_tool_context(), enabled=paths.is_enabled())
    if refusal is not None:
        return refusal
    public_port, local_port = paths.public_port(), paths.local_port()
    check = check_serve(
        find_tailscale=tailscale.find_binary,
        probe_host=tailscale.probe_host,
        probe_serve=tailscale.probe_serve_status,
        health_ok=extension_listening,
        public_port=public_port,
        local_port=local_port,
    )
    store = DeviceStore(paths.db_path())
    try:
        return pair_tool(store=store, check=check, public_port=public_port, image_dir=_port().image_cache_dir())
    finally:
        store.close()


def _file_base_dir(task_id: str) -> Optional[str]:
    """Le dossier de la session d'Hermes contre lequel il résout un chemin relatif (lu dans Hermes).
    Sans Hermes chargeable, None : le crochet garde alors ses replis (TERMINAL_CWD, dossier courant)."""
    try:
        from .adapter import file_base_dir
    except Exception:
        # Un adaptateur qui ne se charge pas ne doit pas faire bloquer tous les outils.
        return None
    return file_base_dir(task_id)


def _guard(tool_name: str = "", args: Any = None, task_id: str = "", **_kwargs: Any) -> Any:
    """Le crochet pre_tool_call : guard_tool_call, avec le dossier de la session d'Hermes."""
    from .core.pair_link import guard_tool_call
    return guard_tool_call(tool_name, args, task_id, base_dir=_file_base_dir)


def _session_env(name: str) -> str:
    """Une variable de la session d'Hermes pour le tour en cours ; vide hors d'Hermes."""
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return ""
    return get_session_env(name, "")


def _turn_context(platform: str = "", parent_session_id: str = "", user_message: Any = None, **_kwargs: Any) -> Any:
    """Le crochet pre_llm_call : la ligne du contexte d'un tour venu de l'app (dit à voix haute,
    réponse coupée, question répondue, appel décroché), qu'Hermes ajoute à la copie du message de
    Léo envoyée au modèle (gardée avec ce message et redonnée aux tours suivants), jamais au prompt
    système. Le message est reconnu à son texte : un message qui coupe Hermes est traité dans le
    tour du premier, dont HERMES_SESSION_MESSAGE_ID reste l'id. Le pont apprend aussi quel
    message ce tour traite. Rien hors de Sheldon, rien pour un sous-agent ; ne lève jamais
    (Hermes le journaliserait à chaque tour)."""
    try:
        if parent_session_id or (platform or _session_env("HERMES_SESSION_PLATFORM")) != PLATFORM_NAME:
            return None
        from .core.turn_context import TURN_CONTEXTS

        message = TURN_CONTEXTS.take(_session_env("HERMES_SESSION_MESSAGE_ID"), user_message)
        if message is None:
            return None
        _turn_took(message.conversation_id, message.message_id)
        return {"context": message.line} if message.line else None
    except Exception:
        return None


def _turn_took(conversation_id: str, message_id: str) -> None:
    """Dit au pont quel message de l'app ce tour d'Hermes traite. Un échec ne coûte jamais la ligne
    du contexte."""
    try:
        from .adapter import turn_took

        turn_took(conversation_id, message_id)
    except Exception:
        pass


def _call(args: Any, **_kwargs: Any) -> str:
    """L'outil sheldon_call : fait sonner l'iPhone de Léo, quand il l'a demandé ou pour un
    élément vraiment important et urgent (garde-fous tenus par l'extension, core/calls.py).
    Il dit la vérité : rien n'est rangé quand Sheldon est désactivé ou que le gateway ne tourne
    pas, puisque rien ne sonnerait."""
    from .core import pair_link, paths
    from .core.calls import call_result
    from .core.runtime import place_call

    if not paths.is_enabled():
        return call_result("disabled")
    if not pair_link.extension_listening(paths.local_port()):
        return call_result("gateway_down")
    return place_call(args, _tool_context(), paths.db_path(), paths.apns_dir(), _port())


def register(ctx: Any) -> None:
    from .core.calls import CALL_SCHEMA, CALL_TOOL
    from .core.pair_link import PAIR_SCHEMA, PAIR_TOOL
    from .core.runtime import CHATS_SCHEMA, CHATS_TOOL, PROPOSE_SCHEMA, PROPOSE_TOOL

    # Chat « maison » des tâches planifiées (--deliver sheldon) : le chat principal. Posé en
    # mémoire pour ce processus, jamais écrit dans .env.
    os.environ.setdefault(HOME_CHANNEL_ENV, "owner")
    ctx.register_platform(
        name=PLATFORM_NAME,
        label="Sheldon",
        adapter_factory=_adapter_factory,
        check_fn=_deps_available,
        is_connected=_is_connected,
        env_enablement_fn=_env_enablement,
        setup_fn=_setup,
        install_hint="hermes sheldon pair",
        platform_hint=PLATFORM_HINT,
        emoji="📱",
        cron_deliver_env_var=HOME_CHANNEL_ENV,
        parse_target_ref_fn=_parse_target,
        validate_target_ref_fn=_validate_target,
        standalone_sender_fn=_standalone_send,
    )
    ctx.register_cli_command(
        name="sheldon",
        help="Relier l'app Sheldon (iPhone, Mac) / Pair the Sheldon app",
        setup_fn=_cli_setup,
        handler_fn=_cli_handle,
        description="Sheldon: QR code, linked devices, notifications, chats / QR code, appareils, notifications, chats",
    )
    ctx.register_skill("blocks", SKILL_PATH, description=SKILL_DESCRIPTION)
    ctx.register_skill("agents", AGENTS_SKILL_PATH, description=AGENTS_SKILL_DESCRIPTION)
    ctx.register_tool(
        name=PROPOSE_TOOL,
        toolset=PLATFORM_NAME,
        schema=PROPOSE_SCHEMA,
        handler=_propose,
        description=PROPOSE_SCHEMA["description"],
        emoji="📱",
    )
    # Le QR code d'appairage, demandé à Hermes sur n'importe quelle messagerie (même jeu d'outils).
    ctx.register_tool(
        name=PAIR_TOOL,
        toolset=PLATFORM_NAME,
        schema=PAIR_SCHEMA,
        handler=_pair,
        description=PAIR_SCHEMA["description"],
        emoji="📱",
    )
    # « Hermes t'appelle » : l'iPhone sonne, quand Léo l'a demandé ou pour un élément urgent.
    ctx.register_tool(
        name=CALL_TOOL,
        toolset=PLATFORM_NAME,
        schema=CALL_SCHEMA,
        handler=_call,
        description=CALL_SCHEMA["description"],
        emoji="📱",
    )
    # Les chats de sujet, créés par Hermes quand Léo le demande (skill sheldon:agents) : la garde
    # ci-dessous bloque « hermes sheldon chats » dans ses commandes (revue finale du plan 6, I3).
    ctx.register_tool(
        name=CHATS_TOOL,
        toolset=PLATFORM_NAME,
        schema=CHATS_SCHEMA,
        handler=_chats,
        description=CHATS_SCHEMA["description"],
        emoji="📱",
    )
    # Avant chaque outil d'Hermes : toute commande ou tout code qui vise Sheldon (sa base, son
    # port, /v1/pair, hermes sheldon, la clé APNs) est bloqué (tâche 16, ronde 2).
    ctx.register_hook("pre_tool_call", _guard)
    # Avant chaque tour d'un message de l'app : son contexte, pour ce tour seulement (plan 6, tâche 1).
    ctx.register_hook("pre_llm_call", _turn_context)
