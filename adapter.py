"""Colle entre Hermes et Sheldon. Seul fichier qui importe Hermes.

Hermes voit Sheldon comme Telegram : des chats privés (un par conversation), des
messages envoyés puis modifiés au fil de l'eau, des questions et des approbations à
boutons, des fichiers. Chaque méthode traduit un appel d'Hermes en un appel du Runtime
(core/runtime.py), où vit tout le reste. HermesLink est le port réel vers Hermes.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import importlib
import logging
import math
import re
import threading
import time
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult

from .core import bots, hostname, media, pair_link, paths, tailscale, welcome
from .core.api import create_app
from .core.bridge import DEFAULT_CURSOR, configured_cursor
from .core.commands import command_parts, command_text, permitted
from .core.conversations import Agent
from .core.files import FileError
from .core.history import MessageLoader
from .core.messages import TEXTS, chat_language, language
from .core.pair_link import GuardedPlaces, check_serve, extension_listening, guarded_places
from .core.pairing import PairingService
from .core.push import PushService
from .core.runtime import Runtime, ToolContext
from .core.server import ApiServer
from .core.store import DeviceStore
from .core.turn_context import TURN_CONTEXTS, TurnContext, is_greeting

try:
    from gateway.config import DEFAULT_STREAMING_CURSOR
except ImportError:  # la constante peut changer de place d'une version d'Hermes à l'autre
    DEFAULT_STREAMING_CURSOR = DEFAULT_CURSOR

logger = logging.getLogger(__name__)

OWNER_USER_ID = "owner"
# La version d'Hermes dont le code a été lu pour les gardes de ce fichier (voir _unsupported_hermes).
VERIFIED_HERMES = "0.21.4"
LOCK_SCOPE = "sheldon"
AGENTS_TTL = 30.0
DEFAULT_APPROVAL_TIMEOUT = 300
# Un appel venu d'une autre boucle attend au plus ce délai que la boucle du gateway l'exécute ;
# au-delà, elle est bloquée et l'appel est abandonné (constat Minor 4, ronde 2 de la relecture
# du lot 12-13), plutôt que de suspendre sans fin l'outil ou le cron qui l'a lancé.
RUNTIME_LOOP_TIMEOUT = 10.0
# Un id de profil Hermes (agents(), profile.name) qui entre dans l'extension : il finit
# dans les pushes (conversationId « agent-<id> ») et dans les URLs de la porte d'entrée
# (/v1/conversations/<id>/...). Hermes borne déjà ses noms de profil à ce même motif
# (hermes_cli/profiles.py:_PROFILE_ID_RE, sauf le profil « default ») mais l'extension ne
# doit jamais dépendre en silence de cette garantie externe : bornage refait ici, à l'entrée.
_UNSAFE_AGENT_ID_CHAR = re.compile(r"[^a-zA-Z0-9_-]")
MAX_AGENT_ID_LENGTH = 64


def _bounded_agent_id(name: str) -> str:
    safe = _UNSAFE_AGENT_ID_CHAR.sub("_", name)[:MAX_AGENT_ID_LENGTH]
    return safe or "agent"


class HermesLink:
    """Le port vers Hermes (voir HermesPort dans core/runtime.py). Chaque appel est protégé :
    une fonction d'Hermes qui change de place ne doit pas faire tomber l'extension.

    Fil d'exécution (vérifié dans Hermes 0.20.4, gateway/run.py ; en 0.21, gateway/run_turn_runner.py) : send_clarify et
    send_exec_approval sont tous deux déclenchés depuis le thread de l'agent (le rappel
    synchrone d'un outil bloquant), mais toujours via safe_schedule_threadsafe(coroutine,
    ctx._loop_for_step) avant d'être attendus avec fut.result(timeout=15) : la coroutine de
    l'adaptateur s'exécute donc sur la boucle du gateway. Ce n'est pas vrai de tout : l'outil
    send_message (tools/send_message_tool.py, via model_tools._run_async) attend adapter.send
    depuis une boucle neuve créée dans un thread de travail, et le repli des crons
    (cron/scheduler.py) lance asyncio.run(...) dans le thread du planificateur. SheldonAdapter
    repasse donc explicitement par la boucle du runtime pour chacun de ces points d'entrée
    (voir SheldonAdapter._on_runtime_loop), plutôt que de supposer qu'ils y sont déjà.
    """

    def __init__(self, gateway_runner: Any = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._runner = gateway_runner
        self._clock = clock
        self._agents: Optional[Tuple[float, List[Agent]]] = None
        self._multiplex: Optional[bool] = None
        self._redact_warned = False
        # Revue finale 47, I2 : pendant le tour d'un agent secondaire, Hermes pose le HERMES_HOME de ce
        # profil (gateway/run.py, _profile_runtime_scope), et get_active_profile_name le suit. Dans le
        # gateway, le principal et son dossier sont donc lus ici, au connect(), hors de tout tour ; hors
        # du gateway (commande, outil), à chaque appel, comme avant.
        self._main: Optional[str] = None
        self._main_home: Optional[Path] = None
        # I3 : les lieux de Sheldon (~/.hermes/sheldon, les dossiers du plugin, le port), figés ici pour
        # la garde des autres profils, jamais relus dans leurs tours. None hors du gateway.
        self._places: Optional[GuardedPlaces] = None
        self._unguarded: Set[str] = set()
        if gateway_runner is not None:
            self._main = _active_profile_name()
            with contextlib.suppress(Exception):
                self._main_home = paths.hermes_home()
            self._places = guarded_places()

    def multiplex(self) -> bool:
        if self._runner is not None:
            return bool(getattr(getattr(self._runner, "config", None), "multiplex_profiles", False))
        if self._multiplex is None:
            # Hors du gateway (outil de l'agent, commande, envoi sans gateway) : la même réponse que
            # le gateway, pour que « sheldon:agent-<profil> », sheldon_propose et « chats add --agent »
            # voient les mêmes agents que lui.
            self._multiplex = _hermes_multiplex()
            if self._multiplex is None:
                self._multiplex = _configured_multiplex()
        return self._multiplex

    def agents(self) -> List[Agent]:
        # list_profiles lit chaque dossier de profil : 30 s de cache suffisent pour l'app.
        now = self._clock()
        if self._agents is not None and now - self._agents[0] < AGENTS_TTL:
            return list(self._agents[1])
        try:
            from hermes_cli.profiles import get_active_profile_name, list_profiles

            current = self._main or get_active_profile_name() or "default"
            is_bot_managed = _bot_mode_probe()
            agents = [
                Agent(
                    id=_bounded_agent_id(profile.name),
                    name=profile.display_name or ("Hermes" if profile.name == "default" else profile.name),
                    description=profile.description or "",
                    model=profile.model,
                    is_default=profile.name == current,
                    home=Path(profile.path),
                    # Le visage Bot Mode du profil (tâche 43) ; le principal garde le nuage.
                    avatar_updated_at=None if profile.name == current else bots.avatar_updated_at(Path(profile.path)),
                    bot_managed=is_bot_managed(Path(profile.path)),
                )
                for profile in list_profiles()
            ]
        except Exception:
            logger.warning("Sheldon: Hermes profiles unavailable", exc_info=True)
            if self._agents is not None:
                # Une panne passagère ne vide pas la liste : l'app croirait sinon chaque agent
                # retiré (agent.deleted, voir core/agent_watch.py). Sans liste lue, aucun agent.
                self._agents = (now, self._agents[1])
                return list(self._agents[1])
            agents = []
        if not self.multiplex():
            # Sans gateway.multiplex_profiles, ce gateway ne sert que son propre profil.
            agents = [agent for agent in agents if agent.is_default]
        self._agents = (now, agents)
        return list(agents)

    def guard_profiles(self, agents: List[Agent]) -> None:
        """Revue finale 47, I3. Hermes 0.21 garde un gestionnaire d'extensions par dossier de profil
        (hermes_cli/plugins.py, get_plugin_manager), et le tour d'un profil passe par le sien
        (invoke_hook, garde pre_tool_call comprise). Le plugin n'est activé que sur le profil par
        défaut (INSTALL-HERMES.md) : sa garde et son contexte de tour sont donc posés ici dans le
        gestionnaire de chaque autre profil servi, la garde avec les lieux figés au démarrage.
        Hors du gateway, rien. Une erreur sur un profil ne touche pas les autres."""
        if self._places is None:
            return
        hooks = _profile_hooks(self._places)
        for agent in agents:
            if agent.is_default or agent.home is None:
                continue
            try:
                _install_hooks(Path(agent.home), hooks)
            except Exception:
                if agent.id not in self._unguarded:
                    logger.warning("Sheldon: guard not installed in the Hermes profile %s", agent.id, exc_info=True)
                self._unguarded.add(agent.id)
            else:
                self._unguarded.discard(agent.id)

    def gateway_state_db(self) -> Path:
        return paths.hermes_state_db()

    def bot_chat_session(self, agent: Agent) -> Optional[str]:
        """La session « Bot Chat » de ce profil (Bot Mode, Hermes 0.21), lue dans sa propre base :
        le titre exact (hermes_state_titles.py, get_session_by_title), gardé seulement si la session
        est cachée, comme Hermes reconnaît le « Bot Chat » canonique (revue finale 47, M1). Jamais
        un « Bot Chat #2 », ni une session visible nommée « Bot Chat » par /title. None sans base,
        sans cette session, ou sur toute erreur."""
        db = Path(agent.home) / "state.db" if agent.home is not None else None
        if db is None or not db.is_file():
            return None
        try:
            from hermes_state import SessionDB

            session_db = SessionDB(db_path=db, read_only=True)
        except Exception:
            logger.warning("Sheldon: Hermes sessions of %s unavailable", agent.id, exc_info=True)
            return None
        try:
            session = session_db.get_session_by_title(bots.BOT_CHAT_TITLE)
        except Exception:
            logger.warning("Sheldon: Bot Chat of %s unreadable", agent.id, exc_info=True)
            return None
        finally:
            with contextlib.suppress(Exception):
                session_db.close()
        session_id = session.get("id") if isinstance(session, dict) and session.get("hidden") else None
        return session_id if isinstance(session_id, str) and session_id else None

    def job_workdir(self, job_id: str) -> Optional[str]:
        """Le dossier de travail d'une tâche planifiée (cron/jobs.py, get_job : workdir), ou None."""
        job = cron_job(job_id)
        workdir = job.get("workdir") if job else None
        return workdir if isinstance(workdir, str) and workdir.strip() else None

    def project_for(self, profile: Optional[str], path: Optional[str]) -> Optional[Dict[str, Any]]:
        """Le projet d'Hermes qui contient ce dossier (hermes_cli/projects_db.py, project_for_path : le
        plus long dossier l'emporte), dans la base de ce profil ($HERMES_HOME/projects.db). La base
        s'ouvre en lecture seule, jamais par projects_db.connect, qui la créerait et la migrerait.
        None sans base, sans projet, ou sur toute erreur."""
        if not path:
            return None
        home = self._profile_home(profile)
        db = home / "projects.db" if home is not None else None
        if db is None or not db.is_file():
            return None
        try:
            import sqlite3

            from hermes_cli.projects_db import project_for_path

            with contextlib.closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                project = project_for_path(conn, path)
        except Exception:
            logger.warning("Sheldon: Hermes projects of %s unavailable", profile or "default", exc_info=True)
            return None
        if project is None:
            return None
        return {key: getattr(project, key, None) for key in ("slug", "name", "icon", "color")}

    def _profile_home(self, profile: Optional[str]) -> Optional[Path]:
        """Le dossier du profil de cet agent ; celui du gateway pour l'agent principal."""
        agent = next((agent for agent in self.agents() if agent.id == profile), None) if profile else None
        if agent is not None and agent.home is not None and not agent.is_default:
            return Path(agent.home)
        if self._main_home is not None:
            return self._main_home
        try:
            return paths.hermes_home()
        except Exception:
            return None

    def link_session(self, session_key: str, session_id: str) -> bool:
        """Fait pointer la clé de session d'un chat de Sheldon vers une session existante, comme
        /resume (gateway/session.py, SessionStore.switch_session), puis oublie l'agent gardé en
        mémoire pour cette clé (gateway/run_agent_cache.py, _evict_cached_agent), qui écrirait sinon
        dans l'ancienne session, et vide son état d'approbation (tools/approval.py, clear_session) :
        un « Autoriser pour la session » d'avant la bascule ne vaut plus après, comme à la frontière
        de /resume (revue finale 47, M1). switch_session refuse une clé que le gateway ne connaît pas
        encore (un chat jamais ouvert). False sur un refus ou une erreur : le chat d'aujourd'hui."""
        store = getattr(self._runner, "session_store", None)
        if store is None:
            return False
        try:
            entry = store.switch_session(session_key, session_id)
        except Exception:
            logger.warning("Sheldon: Hermes refused to point %s at %s", session_key, session_id, exc_info=True)
            return False
        if entry is None:
            return False
        evict = getattr(self._runner, "_evict_cached_agent", None)
        if callable(evict):
            with contextlib.suppress(Exception):
                evict(session_key)
        try:
            from tools.approval import clear_session

            clear_session(session_key)
        except Exception:
            logger.warning("Sheldon: approvals of %s not cleared after the Bot Chat link", session_key, exc_info=True)
        return True

    def session_of(self, session_key: str) -> Optional[str]:
        """La session que vise la clé d'un chat dans le gateway (gateway/session.py,
        SessionStore.peek_session_id) : un /new la change (revue finale 47, M3). None pour une clé
        inconnue, hors du gateway, ou sur toute erreur."""
        store = getattr(self._runner, "session_store", None)
        try:
            session_id = store.peek_session_id(session_key) if store is not None else None
        except Exception:
            return None
        return session_id if isinstance(session_id, str) and session_id else None

    @contextlib.contextmanager
    def open_session_messages(self, db: Path) -> Iterator[MessageLoader]:
        from hermes_state import SessionDB

        # Une connexion par page : l'appel arrive d'un fil de travail (asyncio.to_thread).
        session_db = SessionDB(db_path=Path(db), read_only=True)
        try:
            yield lambda session_id: session_db.get_messages(session_id, include_compacted=True)
        finally:
            with contextlib.suppress(Exception):
                session_db.close()

    def extract_media(self, text: str) -> Tuple[List[Tuple[str, bool]], str]:
        try:
            return BasePlatformAdapter.extract_media(text)
        except Exception:
            return media.extract_media(text)

    def redact(self, text: str) -> str:
        try:
            from agent.redact import redact_sensitive_text

            return redact_sensitive_text(text, force=True)
        except Exception:
            # Sans masquage, un secret d'une commande finirait dans la page Voir : on cache tout.
            if not self._redact_warned:
                logger.warning("Sheldon: Hermes secret redaction failed, action details are hidden", exc_info=True)
                self._redact_warned = True
            return "[redacted]"

    def job_name(self, job_id: str) -> Optional[str]:
        try:
            from cron.jobs import get_job

            job = get_job(job_id)
        except Exception:
            return None
        name = job.get("name") if isinstance(job, dict) else None
        return name if isinstance(name, str) and name.strip() else None

    def resolve_clarify(self, clarify_id: str, response: str) -> bool:
        from tools.clarify_gateway import resolve_gateway_clarify

        return bool(resolve_gateway_clarify(clarify_id, response))

    def resolve_approval(self, session_key: str, choice: str, request_id: Optional[str]) -> int:
        from tools.approval import resolve_gateway_approval

        return int(resolve_gateway_approval(session_key, choice, request_id=request_id))

    def pending_approvals(self, session_key: str) -> Optional[List[Dict[str, Any]]]:
        """Les entrées entières de la file d'Hermes pour cette session (command, description,
        request_id...), de la plus ancienne à la plus récente, lues à la création d'une carte
        et relues au moment d'y répondre (core/requests.py, _approval_target). None si la file
        est illisible : fonction absente ou en erreur. Une liste vide dirait à tort « Hermes
        n'attend rien » (constat Important 1, ronde 2 de la relecture du lot 12-13)."""
        try:
            from tools.approval import list_gateway_approvals

            return [dict(entry) for entry in list_gateway_approvals(session_key)]
        except Exception:
            logger.warning("Sheldon: Hermes approval queue unavailable", exc_info=True)
            return None

    def resume_typing(self, chat_id: str) -> None:
        adapter = _live_adapter()
        if adapter is not None and chat_id:
            adapter.resume_typing_for_chat(chat_id)

    def image_cache_dir(self) -> Path:
        # Les plateformes d'Hermes ne joignent une ligne MEDIA que depuis ses caches.
        try:
            from gateway.platforms.base import get_image_cache_dir

            return Path(get_image_cache_dir())
        except Exception:
            return paths.hermes_home() / "cache" / "images"


_LIVE: Dict[str, Any] = {}


@functools.lru_cache(maxsize=None)
def _profile_hooks(places: GuardedPlaces) -> Tuple[Tuple[str, Callable[..., Any]], ...]:
    """Les crochets posés dans les autres profils : la garde aux lieux figés et le contexte de tour
    (sheldon/__init__.py). Un seul jeu par lieux : une reconnexion du gateway les retrouve déjà posés."""
    from . import _turn_context, frozen_guard

    return (("pre_tool_call", frozen_guard(places)), ("pre_llm_call", _turn_context))


def _install_hooks(home: Path, hooks: Sequence[Tuple[str, Callable[..., Any]]]) -> None:
    """Ajoute ces crochets au gestionnaire d'extensions du profil de ce dossier, s'ils n'y sont pas.
    Comme Hermes pour les crochets de config.yaml (agent/shell_hooks.py, register_from_config :
    manager._hooks.setdefault(event, []).append(callback)), lu sous le HERMES_HOME du profil
    (set_hermes_home_override), la clé du gestionnaire. Puis les y garde après une relecture forcée
    des extensions (_keep_after_forced_reload)."""
    from hermes_cli.plugins import get_plugin_manager
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(home))
    try:
        manager = get_plugin_manager()
    finally:
        reset_hermes_home_override(token)
    _add_hooks(manager, hooks)
    _keep_after_forced_reload(manager, hooks)


def _add_hooks(manager: Any, hooks: Sequence[Tuple[str, Callable[..., Any]]]) -> None:
    for name, callback in hooks:
        callbacks = manager._hooks.setdefault(name, [])
        if callback not in callbacks:
            callbacks.append(callback)


def _keep_after_forced_reload(manager: Any, hooks: Sequence[Tuple[str, Callable[..., Any]]]) -> None:
    """Re-relecture 47, N1, et passe F1. Une découverte forcée des extensions d'un profil (le verbe
    reload-plugins que demande « hermes -p <profil> plugins install|enable|update », ou un outil à qui
    manque un fournisseur) vide son gestionnaire dès son début (unload, hermes_cli/plugins_ledger.py) :
    la garde et la ligne [Sheldon] y manqueraient jusqu'à la relecture suivante des agents (30 s).
    Hermes a eu ce défaut pour ses crochets de config.yaml (#60036) et les repose à la fin de chaque
    découverte forcée, par _re_register_config_hooks_after_force (hermes_cli/plugins.py,
    discover_and_load). Sheldon enveloppe les deux méthodes, une fois par gestionnaire : l'originale,
    puis ses crochets, sans jamais lever. Reposer au retour d'unload ferme aussi la passe elle-même,
    une passe qui lève et le mode sûr (HERMES_SAFE_MODE), qui sautent la repose de fin. Sur un Hermes
    qui n'a pas une méthode, rien pour elle : la relecture des 30 s reste le filet."""
    for name in ("unload", "_re_register_config_hooks_after_force"):
        _restore_after(manager, name, hooks)


def _restore_after(manager: Any, name: str, hooks: Sequence[Tuple[str, Callable[..., Any]]]) -> None:
    """Enveloppe cette méthode du gestionnaire (appelée par self. dans Hermes, donc trouvée sur
    l'instance) : l'originale, puis les crochets de la dernière pose, même si l'originale lève."""
    found = getattr(manager, name, None)
    if found is None:
        return
    if hasattr(found, "sheldon_hooks"):
        found.sheldon_hooks = hooks
        return

    @functools.wraps(found)
    def restore(*args: Any, **kwargs: Any) -> Any:
        try:
            return found(*args, **kwargs)
        finally:
            try:
                _add_hooks(manager, restore.sheldon_hooks)
            except Exception:
                logger.warning("Sheldon: guard not restored after a forced reload of Hermes extensions", exc_info=True)

    restore.sheldon_hooks = hooks
    setattr(manager, name, restore)


def _active_profile_name() -> Optional[str]:
    """Le profil du HERMES_HOME courant (hermes_cli/profiles.py, get_active_profile_name), « default »
    sans nom ; None quand Hermes ne le dit pas : la lecture se refait alors à chaque appel."""
    try:
        from hermes_cli.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception:
        return None


def _hermes_multiplex() -> Optional[bool]:
    """Le multiplex tel que le voient les commandes d'Hermes 0.21 (hermes_cli/gateway_multiplex_mode.py,
    default_gateway_multiplexes) : le registre du gateway vivant (served_profiles), sinon le choix écrit
    dans la config du profil par défaut, sinon coupé. Depuis 0.21 le multiplex est le défaut, et
    gateway.multiplex_profiles reste à None hors du gateway tant qu'il ne l'a pas tranché à son
    démarrage (resolve_multiplex_mode). None quand la réponse est illisible : un Hermes sans ce module,
    une fonction qui lève ou qui ne rend pas un booléen."""
    try:
        from hermes_cli.gateway_multiplex_mode import default_gateway_multiplexes

        answer = default_gateway_multiplexes()
    except Exception:
        return None
    return answer if isinstance(answer, bool) else None


def _configured_multiplex() -> bool:
    """gateway.multiplex_profiles de la config d'Hermes (0.20.4, et le repli en 0.21) ; coupé sans config."""
    try:
        from gateway.config import load_gateway_config

        return bool(getattr(load_gateway_config(), "multiplex_profiles", False))
    except Exception:
        logger.warning("Sheldon: Hermes gateway config unavailable, multiplex considered off", exc_info=True)
        return False


def _bot_mode_probe() -> Callable[[Path], bool]:
    """Le test de Bot Mode d'Hermes 0.21 sur un dossier de profil (tools/bot_mode_probe.py,
    _is_bot_managed : profile.yaml porte ui_meta['hermes-bots']). Un Hermes sans Bot Mode : aucun."""
    try:
        from tools.bot_mode_probe import _is_bot_managed
    except Exception:
        return lambda _home: False

    def managed(home: Path) -> bool:
        try:
            return bool(_is_bot_managed(home))
        except Exception:
            return False

    return managed


def _live_adapter() -> Optional["SheldonAdapter"]:
    return _LIVE.get("adapter")


def _event_message_id(event: Any) -> Optional[str]:
    """Le message de l'app qui a ouvert ce tour (« u-… »), ou None (un tour sans message connu)."""
    value = getattr(event, "message_id", None)
    return value if isinstance(value, str) and value else None


def turn_took(conversation_id: str, message_id: str) -> None:
    """Hermes commence, dans le tour ouvert de cette conversation, un message de l'app arrivé
    pendant ce tour. Appelé par le crochet pre_llm_call, sur le fil de l'agent : le pont le
    nomme sur la boucle du gateway."""
    adapter = _live_adapter()
    if adapter is not None:
        adapter.turn_took(conversation_id, message_id)


def _coerce_plaintext(event: Any) -> None:
    """La réécriture d'Hermes d'un message privé tapé (« restart gateway » devient /restart,
    gateway/platforms/base.py, coerce_plaintext_gateway_command), faite ici pour que la liste
    permise voie la commande qu'Hermes lancerait."""
    try:
        from gateway.platforms.base import coerce_plaintext_gateway_command
    except ImportError:
        return
    coerce_plaintext_gateway_command(event)


def _canonical_command(name: str) -> Optional[str]:
    """Le nom d'Hermes d'une commande qu'il connaît (alias compris), None pour une commande
    inconnue ; le nom lui-même quand la liste d'Hermes est illisible."""
    try:
        from hermes_cli.commands import resolve_command

        command = resolve_command(name)
    except Exception:
        return name
    return command.name if command is not None else None


def _as_conversation(handler: Any) -> Any:
    """Le gestionnaire du gateway pour un message reçu pendant un tour (gateway/run_busy.py,
    _handle_active_session_busy_message) valide une approbation en attente quand le texte entier
    vaut « yes », « ok », « always »..., si l'événement a le droit d'agir sur le gateway. Un texte
    de l'app qui n'est pas une commande lui arrive donc sans ce droit : il reste une phrase, mise
    en file ou qui coupe le tour. Plus tôt, dans handle_message, le même droit lui a permis de
    répondre à une question clarify en attente ; les commandes et les événements d'Hermes
    lui-même (internal) le gardent."""

    async def busy(event: Any, session_key: str) -> Any:
        if not getattr(event, "internal", False) and not (event.text or "").lstrip().startswith("/"):
            event.allow_gateway_control = False
        return await handler(event, session_key)

    return busy


def tool_context() -> ToolContext:
    """La session qui appelle l'outil (sheldon_propose, sheldon_pair), lue dans les variables de session d'Hermes."""
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return ToolContext()
    return ToolContext(
        platform=get_session_env("HERMES_SESSION_PLATFORM", ""),
        chat_id=get_session_env("HERMES_SESSION_CHAT_ID", ""),
        profile=get_session_env("HERMES_SESSION_PROFILE", ""),
        cron_platform=get_session_env("HERMES_CRON_AUTO_DELIVER_PLATFORM", ""),
        cron_chat_id=get_session_env("HERMES_CRON_AUTO_DELIVER_CHAT_ID", ""),
        cron_session=get_session_env("HERMES_CRON_SESSION", ""),
    )


def cron_job(job_id: str) -> Optional[Dict[str, Any]]:
    """L'enregistrement d'une tâche planifiée (cron/jobs.py:2007, get_job), pour la section de
    prompt des tâches qui livrent à Sheldon (core/scheduled.py). None sur toute erreur : la
    section se tait plutôt que de deviner."""
    try:
        from cron.jobs import get_job

        job = get_job(job_id)
    except Exception:
        return None
    return dict(job) if isinstance(job, dict) else None


def workspace_root(task_id: Optional[str]) -> Optional[str]:
    """Le dossier de travail de la session de cette tâche, pour son projet (spec 12) : son cwd
    enregistré, celui que le desktop a posé, ou le terminal.cwd du profil
    (tools/file_tools_paths.py, _authoritative_workspace_root). Jamais le dossier courant du
    processus d'Hermes, qui n'est le dossier d'aucun projet. None sinon."""
    if not task_id:
        return None
    try:
        from tools.file_tools_paths import _authoritative_workspace_root

        root = _authoritative_workspace_root(task_id)
    except Exception:
        return None
    return str(root) if root else None


def file_base_dir(task_id: str) -> Optional[str]:
    """Le dossier contre lequel Hermes résout un chemin relatif d'un outil de fichiers : celui
    que le terminal de la session a enregistré (tools/file_tools.py, _resolve_base_dir), ou à
    défaut le dossier enregistré par le terminal (tools/terminal_tool.py, get_session_cwd)."""
    try:
        from tools.file_tools import _resolve_base_dir

        return str(_resolve_base_dir(task_id or "default"))
    except Exception:
        pass
    try:
        from tools.terminal_tool import get_session_cwd

        return get_session_cwd(task_id or None)
    except Exception:
        return None


def _approval_timeout() -> int:
    """approvals.timeout d'Hermes, l'échéance d'une carte d'approbation : lu dans
    tools/approval_context.py (Hermes 0.21), sinon dans tools/approval.py (0.20.4). Fermé : un
    import qui échoue, une fonction qui lève ou une valeur qui n'est pas un nombre positif donnent
    DEFAULT_APPROVAL_TIMEOUT, le défaut d'Hermes."""
    read = None
    for name in ("tools.approval_context", "tools.approval"):
        try:
            read = getattr(importlib.import_module(name), "_get_approval_timeout", None)
        except Exception:
            read = None
        if callable(read):
            break
    if not callable(read):
        return DEFAULT_APPROVAL_TIMEOUT
    try:
        value = read()
    except Exception:
        return DEFAULT_APPROVAL_TIMEOUT
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) <= 0:
        return DEFAULT_APPROVAL_TIMEOUT
    return int(value)


def _clarify_timeout() -> Optional[int]:
    try:
        from tools.clarify_gateway import get_clarify_timeout

        timeout = int(get_clarify_timeout())
    except Exception:
        return 3600
    return timeout if timeout > 0 else None


def _abandoned() -> SendResult:
    # Pas retryable : la boucle bloquée peut tout de même avoir pris l'appel au dernier moment,
    # et un nouvel essai d'Hermes risquerait alors un message en double.
    return SendResult(success=False, error="Sheldon: gateway loop busy, call abandoned", retryable=False)


def _log_unexpected_kwargs(method: str, kwargs: Dict[str, Any]) -> None:
    # Une méthode future d'Hermes qui ajoute un paramètre ne doit jamais faire échouer l'appel
    # (constat Minor, relecture du lot 12-13) : ignoré, journalisé au niveau debug seulement.
    if kwargs:
        logger.debug("Sheldon: %s received unexpected kwargs from Hermes: %s", method, sorted(kwargs))


class SheldonAdapter(BasePlatformAdapter):
    # Chaque réponse se termine par une modification marquée finale.
    REQUIRES_EDIT_FINALIZE = True
    # Une longue réponse reste une seule bulle (4096 par défaut dans Hermes, comme Telegram).
    MAX_MESSAGE_LENGTH = 100_000

    def __init__(self, config: Any) -> None:
        super().__init__(config=config, platform=Platform("sheldon"))
        extra = getattr(config, "extra", None) or {}
        self._port = int(extra.get("port") or paths.local_port())
        self._store: Optional[DeviceStore] = None
        self._server: Optional[ApiServer] = None
        self._runtime: Optional[Runtime] = None
        self._lock_identity: Optional[str] = None
        self._tasks: Set[asyncio.Task] = set()
        # La boucle du gateway, retenue à connect() : voir _on_runtime_loop.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._welcome: Optional[asyncio.Task] = None

    # Là où le gateway range son gestionnaire des messages reçus pendant un tour
    # (set_busy_session_handler, gateway/run_adapters.py) : toujours enveloppé, voir _as_conversation.
    @property
    def _busy_session_handler(self) -> Any:
        return getattr(self, "_busy_handler", None)

    @_busy_session_handler.setter
    def _busy_session_handler(self, handler: Any) -> None:
        self._busy_handler = None if handler is None else _as_conversation(handler)

    @property
    def authorization_is_upstream(self) -> bool:
        # Chaque appel est déjà authentifié par la clé de son appareil.
        return True

    async def _unsupported_hermes(self) -> Optional[str]:
        """Ce qui manque à ce Hermes pour que la parade contre les approbations par le texte tienne,
        None s'il ne manque rien (revue de la branche, I4). Elle tient à deux internes d'Hermes 0.20.4
        (gateway/platforms/base.py) : set_busy_session_handler range le gestionnaire « occupé » dans
        _busy_session_handler, que handle_message relit (d'où la propriété plus haut), et MessageEvent
        porte allow_gateway_control, que ce gestionnaire lit avant de valider une approbation en
        attente (gateway/run_busy.py, _handle_active_session_busy_message). Qu'une version renomme l'un ou
        l'autre, et un « ok » tapé dans l'app validerait la commande, sans erreur."""
        try:
            fields = {field.name for field in dataclasses.fields(MessageEvent)}
        except TypeError:
            fields = set()
        if "allow_gateway_control" not in fields:
            return "MessageEvent has no allow_gateway_control field"
        seen: List[bool] = []

        async def probe(event: Any, _session_key: str) -> bool:
            seen.append(event.allow_gateway_control)
            return True

        # Le gateway a déjà posé son gestionnaire (gateway/run_adapters.py, avant connect()) : il est remis tel quel.
        previous = getattr(self, "_busy_handler", None)
        try:
            self.set_busy_session_handler(probe)
            handler = getattr(self, "_busy_handler", None)
            if handler is None or handler is previous:
                return "set_busy_session_handler no longer stores the handler in _busy_session_handler"
            event = MessageEvent(
                text="ok", message_type=MessageType.TEXT, source=None, message_id="sheldon-check",
                user_id=OWNER_USER_ID, user_name="Owner", timestamp=datetime.now(),
            )
            await handler(event, "sheldon:check")
        except Exception as error:
            return f"the busy-session handler check failed ({error!r})"
        finally:
            self._busy_handler = previous
        if seen != [False]:
            return "the busy-session handler keeps the right to act on the gateway"
        return None

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if self._server is not None:
            return True
        problem = await self._unsupported_hermes()
        if problem is not None:
            # Échec fermé : sans la parade, Sheldon ne se lance pas comme plateforme.
            version = self._status()["hermesVersion"]
            logger.error(
                "Sheldon: not started. On Hermes %s, %s: a reply typed in the app could approve a waiting "
                "command without Face ID. Sheldon is verified with Hermes %s.", version, problem, VERIFIED_HERMES,
            )
            self._set_fatal_error(
                "hermes_unsupported",
                f"Sheldon: Hermes {version} not supported ({problem}), verified with Hermes {VERIFIED_HERMES}",
                retryable=False,
            )
            return False
        identity = f"port:{self._port}"
        try:
            from gateway.status import acquire_scoped_lock
            acquired, _ = acquire_scoped_lock(LOCK_SCOPE, identity)
        except ImportError:
            acquired = True
        if not acquired:
            self._set_fatal_error(
                "lock_conflict", f"Sheldon: port {self._port} is used by another Hermes profile", retryable=False
            )
            return False
        self._lock_identity = identity
        self._loop = asyncio.get_running_loop()
        try:
            self._store = DeviceStore(paths.db_path())
            runner = getattr(self, "gateway_runner", None)
            # gateway_runner est posé par le gateway après la création de l'adaptateur.
            self._runtime = Runtime(
                store=self._store,
                port=HermesLink(runner),
                submit=self._dispatch_user_message,
                files_dir=paths.files_dir(),
                push=PushService.from_directory(self._store, paths.apns_dir()),
                cursor=configured_cursor(runner, DEFAULT_STREAMING_CURSOR),
            )
            app = create_app(
                store=self._store,
                pairing=PairingService(self._store),
                bridge=self._runtime.bridge,
                hub=self._runtime.hub,
                status_provider=self._status,
                services=self._runtime,
                find_tailscale=tailscale.find_binary,
                probe_host=tailscale.probe_host,
                probe_serve=tailscale.probe_serve_status,
                health_ok=extension_listening,
                public_port=paths.public_port(),
                local_port=self._port,
                is_enabled=paths.is_enabled,
            )
        except Exception:
            self._close_runtime()
            self._release_lock()
            raise
        server = ApiServer(app, host="127.0.0.1", port=self._port)
        try:
            await server.start()
        except OSError as error:
            logger.error("Sheldon: cannot listen on 127.0.0.1:%s: %s", self._port, error)
            self._close_runtime()
            self._release_lock()
            self._set_fatal_error("port_in_use", f"Sheldon: port {self._port} unavailable ({error})", retryable=True)
            return False
        self._server = server
        await self._runtime.start()
        _LIVE["adapter"] = self
        self._mark_connected()
        logger.info("Sheldon: listening on 127.0.0.1:%s", self._port)
        self._start_welcome()
        return True

    def _start_welcome(self) -> None:
        """Le QR code d'une première installation demandée dans un chat (core/welcome.py).

        Lu ici, pendant connect() : le gateway attend la fin de toutes les connexions avant
        d'envoyer son message de redémarrage, puis d'effacer .restart_notify.json
        (gateway/run_startup.py, start(), puis gateway/run_notifications.py, _send_restart_notification)."""
        home = paths.hermes_home()
        try:
            note = welcome.take_target(paths.welcome_note(), home, time.time())
        except Exception:
            logger.warning("Sheldon: welcome note unreadable", exc_info=True)
            return
        if note is None:
            return
        store, port = self._store, self._port
        image_dir = HermesLink(getattr(self, "gateway_runner", None)).image_cache_dir()

        def prepare() -> Tuple[Optional[Path], Optional[str]]:
            # Pas d'offre pour rien : chaque offre annule la précédente, celle de la commande comprise.
            if self._platform_adapter(note.target.platform) is None:
                return None, "platform not connected"
            return welcome.prepare(
                store=store, enabled=paths.is_enabled(), public_port=paths.public_port(), image_dir=image_dir,
                check=lambda: check_serve(
                    find_tailscale=tailscale.find_binary, probe_host=tailscale.probe_host,
                    probe_serve=tailscale.probe_serve_status, health_ok=pair_link.extension_listening,
                    public_port=paths.public_port(), local_port=port,
                ),
            )

        self._welcome = asyncio.ensure_future(welcome.deliver(
            note, prepare=prepare, send_image=self._send_welcome_image,
            notice_pending=lambda: welcome.restart_notice_pending(home),
        ))

    def _platform_adapter(self, platform: str) -> Any:
        adapters = getattr(getattr(self, "gateway_runner", None), "adapters", None) or {}
        try:
            return adapters.get(Platform(platform))
        except ValueError:
            return None

    async def _send_welcome_image(self, target: "welcome.WelcomeTarget", image: Path, caption: str) -> bool:
        """Par l'adaptateur de la plateforme de ce chat, comme la livraison d'Hermes vers une autre
        plateforme (gateway/platforms/webhook.py, _deliver_cross_platform)."""
        adapter = self._platform_adapter(target.platform)
        if adapter is None:
            return False
        metadata = {"thread_id": target.thread_id} if target.thread_id else None
        result = await adapter.send_image_file(target.chat_id, str(image), caption=caption, metadata=metadata)
        return bool(getattr(result, "success", False))

    async def disconnect(self) -> None:
        _LIVE.pop("adapter", None)
        if self._welcome is not None:
            self._welcome.cancel()
            self._welcome = None
        # Le pont refuse d'abord les messages (503, l'app les renverra) : sinon un message
        # accepté pendant la vidange du serveur partirait vers un gateway qui s'arrête. Puis le
        # serveur : une requête servie après l'arrêt du runtime relancerait un envoi (curl
        # compris) et écrirait dans une base fermée (revue finale, M2).
        if self._runtime is not None:
            self._runtime.bridge.close()
        if self._server is not None:
            await self._server.stop()
            self._server = None
        if self._runtime is not None:
            await self._runtime.stop()
        self._close_runtime()
        self._release_lock()
        self._loop = None
        self._mark_disconnected()

    async def _on_runtime_loop(self, coro: "asyncio.Future", abandoned: Any = None) -> Any:
        """Exécute `coro` sur la boucle du gateway captée à connect(), même appelé depuis une
        autre boucle ou un thread sans boucle du tout (constat Important 3, relecture du lot
        12-13) : l'outil send_message attend adapter.send depuis une boucle neuve créée dans
        un thread de travail (tools/send_message_tool.py, model_tools._run_async), et le
        repli des crons lance asyncio.run(...) dans le thread du planificateur
        (cron/scheduler.py). Une file d'événements (EventHub.publish, asyncio.Queue) ou un
        push (PushService, asyncio.ensure_future) déclenchés depuis la mauvaise boucle
        arrivent en retard (réveil manqué, rattrapé seulement par le tic du relais) ou lèvent
        en mode debug asyncio : jamais sur la boucle du runtime, jamais de risque.
        Depuis une autre boucle, l'attente est bornée (RUNTIME_LOOP_TIMEOUT) : au-delà, l'appel
        est abandonné (il ne s'exécutera plus) et `abandoned` est rendu à Hermes."""
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if self._loop is None or current is self._loop:
            return await coro
        gave_up = threading.Event()

        async def unless_abandoned() -> Any:
            # Annuler la future ne suffit pas : asyncio replanifie cette annulation derrière le
            # premier pas de la tâche (futures._chain_future), et les corps _send, _send_file...
            # n'attendent rien, ils s'exécuteraient donc en entier, en retard, une fois la boucle
            # libérée. Ce drapeau, lu au démarrage sur la boucle du gateway, les en empêche.
            if gave_up.is_set():
                coro.close()
                return abandoned
            return await coro

        future = asyncio.run_coroutine_threadsafe(unless_abandoned(), self._loop)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), RUNTIME_LOOP_TIMEOUT)
        except asyncio.TimeoutError:
            gave_up.set()
            logger.warning("Sheldon: the gateway loop did not take a call within %.0f s, call abandoned", RUNTIME_LOOP_TIMEOUT)
            return abandoned

    # Hermes vers l'app.

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        # reply_to : le message de l'app auquel Hermes répond (l'ancre de son tour, event.message_id).
        return await self._on_runtime_loop(self._send(chat_id, content, metadata, reply_to), _abandoned())

    async def _send(
        self, chat_id: str, content: str, metadata: Optional[Dict[str, Any]], reply_to: Optional[str] = None
    ) -> SendResult:
        if self._runtime is None:
            return SendResult(success=False, error="Sheldon not connected", retryable=True)
        return SendResult(success=True, message_id=self._runtime.sent(chat_id, content, metadata, reply_to))

    async def edit_message(self, chat_id: str, message_id: str, content: str, *, finalize: bool = False) -> SendResult:
        return await self._on_runtime_loop(self._edit_message(chat_id, message_id, content, finalize), _abandoned())

    async def _edit_message(self, chat_id: str, message_id: str, content: str, finalize: bool) -> SendResult:
        if self._runtime is None:
            return SendResult(success=False, error="Sheldon not connected", retryable=True)
        self._runtime.edited(chat_id, message_id, content, finalize)
        return SendResult(success=True, message_id=message_id)

    async def send_typing(self, chat_id: str, metadata: Any = None) -> None:
        await self._on_runtime_loop(self._send_typing(chat_id))

    async def _send_typing(self, chat_id: str) -> None:
        if self._runtime is not None:
            self._runtime.typing(chat_id)

    async def on_processing_start(self, event: MessageEvent) -> None:
        if self._runtime is not None:
            self._runtime.turn_started(event.source.chat_id, _event_message_id(event))

    async def on_processing_complete(self, event: MessageEvent, outcome: Any) -> None:
        if self._runtime is not None:
            self._runtime.turn_finished(
                event.source.chat_id, str(getattr(outcome, "value", outcome)), _event_message_id(event)
            )

    def format_tool_event(self, event: Any, *, mode: str = "all", preview_max_len: int = 40) -> Optional[str]:
        # Pas de bulles « ⚙️ outil... » dans l'app : les actions se lisent dans la page Voir.
        return None

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> SendResult:
        _log_unexpected_kwargs("send_clarify", _kwargs)
        if self._runtime is None:
            return SendResult(success=False, error="Sheldon not connected", retryable=True)
        request = self._runtime.clarify(
            chat_id=chat_id, question=question, choices=choices, clarify_id=clarify_id,
            session_key=session_key, timeout=_clarify_timeout(), cwd=self._session_folder(session_key),
        )
        return SendResult(success=True, message_id=request.id)

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
        allow_permanent: bool = True,
        allow_session: bool = True,
        smart_denied: bool = False,
        **_kwargs: Any,
    ) -> SendResult:
        _log_unexpected_kwargs("send_exec_approval", _kwargs)
        if self._runtime is None:
            return SendResult(success=False, error="Sheldon not connected", retryable=True)
        request = self._runtime.approval(
            chat_id=chat_id, command=command, description=description, session_key=session_key,
            allow_session=allow_session, allow_permanent=allow_permanent, timeout=_approval_timeout(),
            cwd=self._session_folder(session_key),
        )
        return SendResult(success=True, message_id=request.id)

    def _session_folder(self, session_key: str) -> Optional[str]:
        """Le dossier de travail de la session de ce tour (spec 12) : le gateway donne aux outils
        l'id de la session comme task_id (gateway/run_turn_runner.py), retrouvé par sa clé
        (gateway/session.py, SessionStore.peek_session_id). None sur toute erreur."""
        store = getattr(getattr(self, "gateway_runner", None), "session_store", None)
        try:
            session_id = store.peek_session_id(session_key) if store is not None else None
        except Exception:
            return None
        return workspace_root(session_id) if isinstance(session_id, str) else None

    async def send_image_file(self, chat_id: str, image_path: str, caption: Optional[str] = None, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, **_kwargs: Any) -> SendResult:
        return await self._on_runtime_loop(self._send_file(chat_id, image_path, "image", caption), _abandoned())

    async def send_document(self, chat_id: str, file_path: str, caption: Optional[str] = None, file_name: Optional[str] = None, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, **_kwargs: Any) -> SendResult:
        return await self._on_runtime_loop(self._send_file(chat_id, file_path, "document", caption), _abandoned())

    async def send_video(self, chat_id: str, video_path: str, caption: Optional[str] = None, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, **_kwargs: Any) -> SendResult:
        return await self._on_runtime_loop(self._send_file(chat_id, video_path, "video", caption), _abandoned())

    async def send_voice(self, chat_id: str, audio_path: str, caption: Optional[str] = None, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, **_kwargs: Any) -> SendResult:
        return await self._on_runtime_loop(self._send_file(chat_id, audio_path, "audio", caption), _abandoned())

    async def play_tts(self, chat_id: str, audio_path: str, **_kwargs: Any) -> SendResult:
        return await self._on_runtime_loop(self._play_tts(chat_id, audio_path), _abandoned())

    async def _play_tts(self, chat_id: str, audio_path: str) -> SendResult:
        if self._runtime is None:
            return SendResult(success=False, error="Sheldon not connected", retryable=True)
        try:
            self._runtime.voice_reply(chat_id, audio_path)
        except FileError as error:
            return SendResult(success=False, error=error.code)
        return SendResult(success=True)

    async def _send_file(self, chat_id: str, path: str, kind: str, caption: Optional[str]) -> SendResult:
        if self._runtime is None:
            return SendResult(success=False, error="Sheldon not connected", retryable=True)
        try:
            message_id = self._runtime.file_sent(chat_id, path, kind, caption)
        except FileError as error:
            logger.warning("Sheldon: %s not sent (%s)", Path(path).name, error.code)
            return SendResult(success=False, error=error.code)
        return SendResult(success=True, message_id=message_id)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        name = self._runtime.conversation_for_chat(chat_id).title if self._runtime is not None else "Sheldon"
        return {"name": name, "type": "dm"}

    # L'app vers Hermes.

    async def _dispatch_user_message(
        self, text: str, message_id: str, conversation_id: str = "main", input_mode: str = "text",
        context: Optional[TurnContext] = None, gateway_control: bool = True,
    ) -> None:
        if self._message_handler is None or self._runtime is None:
            raise RuntimeError("Hermes gateway is not ready")
        route = self._runtime.route(conversation_id)
        # message_id : Hermes le pose dans HERMES_SESSION_MESSAGE_ID pour ce tour, que lit le crochet
        # pre_llm_call (__init__.py) pour trouver le contexte de ce message.
        source = self.build_source(
            chat_id=route.chat_id, chat_name=route.chat_name, chat_type="dm", user_id=OWNER_USER_ID, user_name="Owner",
            message_id=message_id,
        )
        if route.profile is not None:
            # Mode multiplex : ce message va à un autre profil d'Hermes (gateway/run.py,
            # _resolve_profile_home_for_source).
            source.profile = route.profile
        # Décision P6-10 : le salut du décroché (callId), celui de l'utilisateur ou « Allô ? » que l'app dit seule
        # sans raison lisible, ne coupe pas Hermes quand une commande de cette conversation attend son
        # accord (la coupure la refuserait) : il part comme un texte, fondu dans le tour, et la commande
        # attend toujours sa carte. Seule une phrase qui dit quelque chose la refuse (P6-2). Étendue :
        # le salut peut nommer l'agent qui appelle (« Allô Hermes ? »), relu dans CallStore.
        agent_name = self._runtime.call_agent_name(context.call_id, conversation_id) if context is not None and context.call_id is not None else None
        spoken = input_mode == "voice" and not (
            context is not None and context.call_id is not None and is_greeting(text, agent_name=agent_name)
            and self._runtime.command_waits(conversation_id)
        )
        event = MessageEvent(
            text=text,
            message_type=MessageType.VOICE if spoken else MessageType.TEXT,
            source=source,
            message_id=message_id,
            user_id=OWNER_USER_ID,
            user_name="Owner",
            timestamp=datetime.now(),
            # Rien de ce qui est dit iPhone verrouillé n'agit sur le gateway d'Hermes : ni commande, ni
            # réponse à une question clarify en attente (décision P4-11). Pas plus ce que l'utilisateur n'a pas
            # écrit tel quel : la réponse à une proposition, écrite par Sheldon, et le premier message
            # d'un appel décroché (callId), « Allô ? » que l'app dit seule sans raison lisible (revue
            # finale du plan 6, I2). Un texte simple perd aussi ce droit au gestionnaire « occupé »
            # (_as_conversation).
            allow_gateway_control=gateway_control and not (
                context is not None and (context.device_locked or context.call_id is not None)
            ),
        )
        if event.allow_gateway_control:
            _coerce_plaintext(event)
        command = command_text(event.text)
        if command is not None:
            if not self._permitted(command):
                # Décision P6-4 : Hermes ne reçoit pas la commande, Sheldon renvoie au terminal.
                self._refuse_from_the_app(conversation_id)
                return
            # La commande de base, sans ce qui la masquait : celle qu'Hermes lance.
            event.text = command
        if route.channel_prompt is not None:
            # Conversation d'une carte du fil : le texte livré, en contexte pour ce tour seulement
            # (Hermes ne l'enregistre pas dans l'historique).
            event.channel_prompt = route.channel_prompt
        task = asyncio.create_task(self.handle_message(event))
        self._tasks.add(task)
        task.add_done_callback(self._on_dispatch_done)

    def _refuse_from_the_app(self, conversation_id: str) -> None:
        """La phrase qui renvoie au terminal, dans la langue du chat (ses derniers messages), après
        l'écho du message de l'utilisateur, que le pont publie au retour de _dispatch_user_message."""
        texts = TEXTS[chat_language(TURN_CONTEXTS.texts(conversation_id), language())]
        bridge = self._runtime.bridge
        asyncio.get_running_loop().call_soon(bridge.assistant_sent, texts["command_from_terminal"], conversation_id)

    def _hermes_command(self, text: str) -> Optional[Tuple[str, str]]:
        """La commande qu'Hermes lancerait pour ce texte, et ses arguments : son nom d'Hermes (alias
        compris), ou, pour un nom qu'Hermes ne connaît pas, celui d'un raccourci de config.yaml
        (quick_commands de type alias), que le gateway développe avant de lancer la commande
        (gateway/run_inbound.py, _handle_message). None pour un texte qui n'est pas une commande."""
        name, args = command_parts(text)
        if name is None:
            return None
        canonical = _canonical_command(name)
        if canonical is not None:
            return canonical, args
        shortcut = self._quick_commands().get(name)
        if isinstance(shortcut, dict) and shortcut.get("type") == "alias":
            target, target_args = command_parts("/" + str(shortcut.get("target") or "").strip().lstrip("/"))
            if target is not None:
                return _canonical_command(target) or target, f"{target_args} {args}".strip()
        return name, args

    def _permitted(self, command: str) -> bool:
        """La commande qu'Hermes lancerait pour ce texte est dans la liste permise (core/commands.py)."""
        resolved = self._hermes_command(command)
        return resolved is not None and permitted(*resolved)

    def _quick_commands(self) -> Dict[str, Any]:
        config = getattr(getattr(self, "gateway_runner", None), "config", None)
        commands = config.get("quick_commands") if isinstance(config, dict) else getattr(config, "quick_commands", None)
        return commands if isinstance(commands, dict) else {}

    def turn_took(self, conversation_id: str, message_id: str) -> None:
        loop, runtime = self._loop, self._runtime
        if loop is not None and runtime is not None:
            loop.call_soon_threadsafe(runtime.bridge.turn_took, conversation_id, message_id)

    def _on_dispatch_done(self, task: "asyncio.Task[None]") -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Sheldon: le traitement du message a échoué", exc_info=error)

    def _status(self) -> Dict[str, Any]:
        try:
            hermes_version = metadata.version("hermes-agent")
        except metadata.PackageNotFoundError:
            hermes_version = "unknown"
        return {"hermesVersion": hermes_version, "serverName": hostname.display_name()}

    def _close_runtime(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None
        if self._store is not None:
            self._store.close()
            self._store = None

    def _release_lock(self) -> None:
        if self._lock_identity is None:
            return
        with contextlib.suppress(ImportError):
            from gateway.status import release_scoped_lock
            release_scoped_lock(LOCK_SCOPE, self._lock_identity)
        self._lock_identity = None
