"""« Hermes t'appelle » : l'outil sheldon_call, ses garde-fous et le registre des appels.

Hermes appelle l'utilisateur quand il le lui a demandé (« appelle-moi quand tu as fini »), ou quand
un élément est vraiment important et urgent. L'iPhone sonne par un push VoIP (PushKit, puis
CallKit dans l'app) ; les autres appareils reçoivent une alerte. Les garde-fous sont tenus
ici, jamais par l'agent : appels coupés dans l'app (callsAllowed), heures calmes de chaque
appareil (sauf appel demandé), et au plus un appel non demandé par heure. Le registre vit
dans sheldon.db : l'outil tourne dans n'importe quel processus d'Hermes, le gateway envoie.

Sécurité (au-delà du brief d'origine) : `requested` n'est qu'un mot donné par l'agent à
l'outil, jamais vérifié par ailleurs. Un agent manipulé qui le déclarerait toujours vrai
contournerait sinon complètement la limite horaire des appels non demandés et les heures
calmes. Les appels demandés sont donc bornés eux aussi (MAX_REQUESTED_PER_HOUR), et chaque
tentative évaluée par les garde-fous est journalisée par l'appelant (runtime.place_call),
sans jamais y mettre la raison.

Ronde de sécurité (task-17-findings-r1.md) :
- les plafonds sont comptés et l'appel écrit (avec sa ligne d'outbox) dans une seule
  transaction BEGIN IMMEDIATE : le verrou d'écriture de sheldon.db est pris avant de compter,
  entre fils comme entre processus (sous-agents de delegate_task, sessions, tâches planifiées) ;
- au relais, le gateway refait callsAllowed et les heures calmes à l'heure du relais, et un
  appel de plus de CALL_RING_WINDOW_SECONDS ne sonne jamais (statut « missed ») ;
- au plus MAX_REQUESTED_PER_QUIET_PERIOD appels demandés par période calme d'un appareil ;
- requested n'est cru que dans un tour ouvert par un message de l'utilisateur (runtime.place_call).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .outbox import Outbox, append_row
from .sqlite import SqliteStore
from .store import PushTarget

CALL_TOOL = "sheldon_call"
MAX_REASON_LENGTH = 200
UNREQUESTED_WINDOW_SECONDS = 3600
# Sécurité : borne aussi les appels demandés, pour la même fenêtre d'une heure (voir le
# docstring du module).
MAX_REQUESTED_PER_HOUR = 3
# Pendant les heures calmes d'un appareil, au plus 2 appels demandés par période calme, en plus
# du plafond horaire (décision du contrôleur, ronde de sécurité).
MAX_REQUESTED_PER_QUIET_PERIOD = 2
# Un appel relayé plus tard que ceci ne sonne plus : il passe en « missed » (gateway arrêté,
# relais en retard). Un appel en retard ne sert à rien.
CALL_RING_WINDOW_SECONDS = 60
CALL_RETENTION_SECONDS = 30 * 86400
AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_CLOCK_TIME = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")

CALL_SCHEMA: Dict[str, Any] = {
    "name": CALL_TOOL,
    "description": (
        "Call the user through the Sheldon app: their iPhone rings like a phone call and answering opens a "
        "voice conversation with you; their Mac shows an alert. Use it in two cases only: the user asked you "
        "to call them (for example 'call me when you are done'), then set requested to true; or something is "
        "truly important and urgent and cannot wait for a normal message, then set requested to false. Never "
        "call for ordinary information, a routine summary or a finished task the user did not ask to be "
        "called about. The result says whether the call was placed: Sheldon refuses when the user turned "
        "calls off, during their quiet hours unless they asked for the call, for more than one call per hour "
        "that they did not ask for, and even for a call they did ask for beyond a few per hour or two per "
        "night of quiet hours (a safeguard in case this tool is ever called on its own, without the user "
        "really asking). requested only counts in a conversation turn started by the user's own message; "
        "in a scheduled task or an automatic session, the call counts as not requested. If the call is not "
        "placed, tell the user in your normal reply instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One line shown on the call screen: why you are calling (at most 200 characters).",
            },
            "requested": {
                "type": "boolean",
                "description": "true only when the user explicitly asked you to call them; false when you call on your own because it is urgent.",
            },
            "agentId": {
                "type": "string",
                "description": "Optional: the Sheldon agent that calls, when it is not the agent of this conversation.",
            },
        },
        "required": ["reason", "requested"],
    },
}

# Ce que l'outil répond à Hermes, en anglais comme le reste de ce qu'il lit.
CALL_RESULTS = {
    "calling": "The call is placed: {ringing} device(s) will ring and {alerted} will show an alert. Do not send a separate message about the call.",
    "invalid": "Not called: give a one-line reason (at most 200 characters), requested (true or false) and, if you set it, a Sheldon agentId.",
    "unknown_agent": "Not called: there is no Sheldon agent {agent!r}.",
    "push_off": "Not called: notifications are not set up on this computer. Ask the user to run hermes sheldon push setup themselves, over SSH, and tell them in your normal reply instead.",
    "disabled": "Not called: Sheldon is disabled on this computer. Tell the user in your normal reply instead.",
    "gateway_down": "Not called: the Sheldon extension is not running (the Hermes gateway is stopped), so nothing would ring. Tell the user in your normal reply instead.",
    "unavailable": "Not called: Sheldon's storage is busy right now. Tell the user in your normal reply instead.",
    "quiet_hours_limit": "Not called: the user already got two calls they asked for during these quiet hours. Tell the user in your normal reply instead.",
    "no_device": "Not called: no Sheldon device has registered for notifications yet. Tell the user in your normal reply instead.",
    "calls_off": "Not called: the user turned calls off in the Sheldon app. Tell the user in your normal reply instead.",
    "quiet_hours": "Not called: it is quiet hours on the user's devices, when only a call they asked for can ring. Tell the user in your normal reply instead.",
    "rate_limited": "Not called: you already called the user without being asked less than an hour ago. Tell the user in your normal reply instead.",
    "too_many_requested": "Not called: too many calls the user asked for were already placed in the last hour. Tell the user in your normal reply instead.",
}
REQUESTED_IGNORED = "requested was counted as false: this turn was not started by a message from the user."


def call_result(status: str, call_id: Optional[str] = None, *, note: Optional[str] = None, **values: Any) -> str:
    """Ce que l'outil rend à Hermes : ok, status, message (et callId, note)."""
    body: Dict[str, Any] = {"ok": status == "calling", "status": status, "message": CALL_RESULTS[status].format(**values)}
    if call_id is not None:
        body["callId"] = call_id
    if note is not None:
        body["note"] = note
    return json.dumps(body)


def parse_quiet_hours(value: object) -> Optional[Dict[str, str]]:
    """Les heures calmes envoyées par l'app : {"start": "22:00", "end": "07:00", "timeZone": "Europe/Paris"}.

    None les retire. Lève ValueError pour tout autre contenu (heure hors format, fuseau inconnu).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("quietHours")
    start, end, zone = value.get("start"), value.get("end"), value.get("timeZone")
    if not all(isinstance(part, str) for part in (start, end, zone)):
        raise ValueError("quietHours")
    if not _CLOCK_TIME.fullmatch(start) or not _CLOCK_TIME.fullmatch(end):
        raise ValueError("quietHours")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("quietHours") from None
    return {"start": start, "end": end, "timeZone": zone}


def _minutes(clock_time: str) -> int:
    hours, minutes = clock_time.split(":")
    return int(hours) * 60 + int(minutes)


def _local_window(quiet: Dict[str, str], now: float) -> Tuple[datetime, int, int]:
    """L'heure locale de l'appareil, puis start et end en minutes. Lève pour une valeur illisible."""
    local = datetime.fromtimestamp(now, ZoneInfo(quiet["timeZone"]))
    return local, _minutes(quiet["start"]), _minutes(quiet["end"])


def in_quiet_hours(quiet: Optional[Dict[str, str]], now: float) -> bool:
    """Heure locale de l'appareil dans l'intervalle [start, end[ ; il peut passer minuit, start == end n'en fait pas."""
    if quiet is None:
        return False
    try:
        local, start, end = _local_window(quiet, now)
    except Exception:
        # Un fuseau devenu illisible, une base modifiée à la main : dans le doute, l'appareil dort.
        return True
    minute = local.hour * 60 + local.minute
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def quiet_period_start(quiet: Optional[Dict[str, str]], now: float) -> Optional[float]:
    """Le début de la période calme en cours sur l'appareil, ou None hors des heures calmes.

    Illisible : les dernières 24 heures, pour compter large."""
    if not in_quiet_hours(quiet, now):
        return None
    try:
        local, start, _end = _local_window(quiet, now)
    except Exception:
        return now - 86400
    day = local.date() if local.hour * 60 + local.minute >= start else local.date() - timedelta(days=1)
    begin = datetime.combine(day, clock_time(start // 60, start % 60), tzinfo=local.tzinfo)
    return begin.timestamp()


@dataclass(frozen=True)
class CallPlan:
    """Qui sonne (voip), qui reçoit une alerte (alerts), ou pourquoi personne (status)."""

    status: str
    voip: List[PushTarget] = field(default_factory=list)
    alerts: List[PushTarget] = field(default_factory=list)


def _night_limit_reached(target: PushTarget, now: float, requested_since: Optional[Callable[[float], int]]) -> bool:
    start = quiet_period_start(target.quiet_hours, now) if requested_since is not None else None
    return start is not None and requested_since(start) >= MAX_REQUESTED_PER_QUIET_PERIOD


def plan_call(
    targets: List[PushTarget],
    *,
    requested: bool,
    now: float,
    last_unrequested_at: Optional[float],
    requested_count: int = 0,
    requested_since: Optional[Callable[[float], int]] = None,
) -> CallPlan:
    """requested_count : combien d'appels demandés ont déjà été placés dans la dernière heure
    (CallStore.requested_calls_since), pour le garde-fou de sécurité au-delà du brief : sans
    lui, un agent manipulé qui déclarerait toujours requested=true contournerait la limite
    horaire ci-dessous et les heures calmes. requested_since(t) : combien d'appels demandés
    depuis t, pour la limite de la période calme de chaque appareil (sans lui, pas de limite)."""
    if not targets:
        return CallPlan("no_device")
    allowed = [target for target in targets if target.calls_allowed]
    if not allowed:
        return CallPlan("calls_off")
    if requested:
        if requested_count >= MAX_REQUESTED_PER_HOUR:
            return CallPlan("too_many_requested")
        awake = [target for target in allowed if not _night_limit_reached(target, now, requested_since)]
        if not awake:
            return CallPlan("quiet_hours_limit")
    else:
        if last_unrequested_at is not None and now - last_unrequested_at < UNREQUESTED_WINDOW_SECONDS:
            return CallPlan("rate_limited")
        awake = [target for target in allowed if not in_quiet_hours(target.quiet_hours, now)]
        if not awake:
            return CallPlan("quiet_hours")
    return CallPlan(
        "calling",
        voip=[target for target in awake if target.voip_token],
        alerts=[target for target in awake if not target.voip_token],
    )


@dataclass(frozen=True)
class Call:
    id: str
    agent_id: str
    agent_name: str
    conversation_id: str
    reason: str
    requested: bool
    created_at: float
    # placed (rangé, pas encore relayé), rung (le gateway l'a fait sonner), missed (relayé trop
    # tard, ou plus aucun appareil ne l'accepte à l'heure du relais : rien n'a sonné).
    status: str = "placed"

    def to_json(self) -> Dict[str, Any]:
        """Ce que portent le push VoIP et l'alerte (contrat v1, « Appels »)."""
        return {
            "callId": self.id,
            "agentId": self.agent_id,
            "agentName": self.agent_name,
            "conversationId": self.conversation_id,
            "reason": self.reason,
            "requested": self.requested,
        }


_CALL_COLUMNS = "id, agent_id, agent_name, conversation_id, reason, requested, created_at, status"


class CallStore(SqliteStore):
    SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    requested INTEGER NOT NULL,
    created_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'placed'
);
""" + Outbox.SCHEMA

    def place(
        self,
        targets: List[PushTarget],
        *,
        requested: bool,
        agent_id: str,
        agent_name: str,
        conversation_id: str,
        reason: str,
    ) -> Tuple[CallPlan, Optional[Call]]:
        """Les garde-fous, puis l'appel et sa ligne d'outbox call.placed, dans une seule
        transaction BEGIN IMMEDIATE : le verrou d'écriture de sheldon.db est pris avant de
        compter, donc deux appels simultanés (fils ou processus) ne passent pas le même plafond.
        Un plan refusé n'écrit rien. Les appels de plus de CALL_RETENTION_SECONDS sont purgés."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._clock()
                plan = plan_call(
                    targets, requested=requested, now=now, last_unrequested_at=self._last_unrequested_at(),
                    requested_count=self._requested_since(now - UNREQUESTED_WINDOW_SECONDS),
                    requested_since=self._requested_since,
                )
                if plan.status != "calling":
                    self._conn.execute("ROLLBACK")
                    return plan, None
                call = Call(f"c-{uuid.uuid4().hex}", agent_id, agent_name, conversation_id, reason, requested, now)
                self._conn.execute("DELETE FROM calls WHERE created_at < ?", (now - CALL_RETENTION_SECONDS,))
                self._conn.execute(
                    "INSERT INTO calls (id, agent_id, agent_name, conversation_id, reason, requested, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (call.id, agent_id, agent_name, conversation_id, reason, int(requested), now),
                )
                append_row(self._conn, "call.placed", {
                    "callId": call.id,
                    "voip": [target.device_id for target in plan.voip],
                    "alerts": [target.device_id for target in plan.alerts],
                }, now)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
        return plan, call

    def get(self, call_id: str) -> Optional[Call]:
        with self._lock:
            row = self._conn.execute(f"SELECT {_CALL_COLUMNS} FROM calls WHERE id = ?", (call_id,)).fetchone()
        if row is None:
            return None
        return Call(
            row["id"], row["agent_id"], row["agent_name"], row["conversation_id"], row["reason"], bool(row["requested"]),
            row["created_at"], row["status"],
        )

    def settle(self, call_id: str, status: str) -> bool:
        """placed devient rung ou missed, une seule fois : un relais rejoué ne sonne pas deux fois."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE calls SET status = ? WHERE id = ? AND status = 'placed'", (status, call_id)
            )
            return cursor.rowcount == 1

    def _last_unrequested_at(self) -> Optional[float]:
        row = self._conn.execute("SELECT MAX(created_at) AS at FROM calls WHERE requested = 0").fetchone()
        return row["at"] if row is not None else None

    def _requested_since(self, since: float) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM calls WHERE requested = 1 AND created_at >= ?", (since,)
        ).fetchone()
        return row["n"] if row is not None else 0

    def last_unrequested_at(self) -> Optional[float]:
        """L'heure du dernier appel que l'utilisateur n'avait pas demandé, s'il y en a un."""
        with self._lock:
            return self._last_unrequested_at()

    def requested_calls_since(self, since: float) -> int:
        """Combien d'appels demandés (requested=true) sont déjà partis depuis `since` : le
        garde-fou de sécurité qui borne aussi les appels demandés (voir le docstring du module),
        pour qu'un agent manipulé ne contourne pas la limite horaire en déclarant toujours
        requested=true."""
        with self._lock:
            return self._requested_since(since)
