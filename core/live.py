"""Le pilote de l'Activité en direct des décisions importantes (spec du 2 octobre 2026).

Une seule Activité à la fois, par appareil iOS relié (DeviceStore.live_targets()) : une
décision importante créée en démarre une nouvelle (D3), une décision montrée qui se ferme
bascule vers la plus récente autre décision importante en attente ou termine l'Activité, et
une autre décision importante qui se ferme sans être montrée ne fait bouger que son compte
« others ». Tout ce module est pur (aucune E/S) : plan_live et plan_registered rendent ce
qu'il faut pousser et ce qu'il faut ranger dans live_activities, à charge pour l'appelant
(PushService, core/push.py) de le faire. La table « Quand l'extension pousse » de la spec
est reprise ligne à ligne dans plan_live et _close_shown.

`sender` est un résolveur (Request -> str), pas une chaîne : une bascule vers une autre
décision importante en attente peut appartenir à une tout autre conversation que celle qui
vient de se fermer, et ce module ne doit dépendre d'aucun catalogue pour rester pur. C'est le
seul écart avec le paramètre `sender` à plat de la spec (E4) ; le rapport de tâche le signale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .push import live_end_payload, live_start_payload, live_state, live_update_payload
from .requests import Request
from .store import LiveTarget

SenderOf = Callable[[Request], str]


@dataclass(frozen=True)
class LiveSend:
    device_id: str
    token: str
    environment: str
    payload: Dict[str, Any]
    priority: int
    expiration: Optional[int]
    # Quel jeton de l'appareil ce push a emprunté : PushService._deliver_live s'en sert pour
    # savoir où écrire quand Apple le refuse (start_token ou activity_token, même colonne pour
    # clear_live_token, mais le distinguo reste utile à la lecture et au journal).
    token_kind: str  # "start" | "activity"


@dataclass(frozen=True)
class ShownChange:
    """À ranger dans live_activities (DeviceStore.set_live_shown) après l'envoi."""

    device_id: str
    request_id: Optional[str]
    forget_activity_token: bool


def _others_count(important: List[Request], shown_id: Optional[str]) -> int:
    """Les décisions importantes en attente autres que celle qu'on montre."""
    return len([r for r in important if r.id != shown_id])


def _end_state_for_superseded(request_id: str, others: int) -> Dict[str, Any]:
    """Une Activité qu'on termine parce qu'une autre, plus récente, la remplace (D3) : sa
    demande n'est pas forcément close (elle peut rester en attente dans l'onglet Demandes),
    mais plan_live est pur et ne relit jamais son contenu d'origine. `expired` est la valeur
    la plus proche des trois permises par le statut (pending/answered/expired) pour une
    Activité qui s'efface sans réponse de l'utilisateur ; ni sender ni title n'ont plus d'importance une
    fois l'événement `end` reçu (dismissal-date passée, F4)."""
    return {
        "requestId": request_id, "sender": None, "title": None, "category": None,
        "primary": None, "others": others, "expiresAt": None, "status": "expired",
    }


def _close_shown(
    target: LiveTarget, important: List[Request], sender: SenderOf, preview: bool, now: float
) -> Tuple[List[LiveSend], List[ShownChange]]:
    """La demande que `target` montre vient de ne plus attendre (répondue, expirée, ou
    jamais valable au moment du PUT) : bascule vers la plus récente décision importante encore
    en attente, ou termine l'Activité s'il n'y en a aucune. Sans jeton d'Activité, rien ne part
    (F11) : l'app rangera au prochain réveil, stale-date grisant déjà les boutons."""
    if target.shown_request_id is None or target.activity_token is None:
        return [], []
    if important:
        newest = important[0]
        state = live_state(newest, sender(newest), _others_count(important, newest.id), preview)
        send = LiveSend(target.device_id, target.activity_token, target.environment, live_update_payload(state, now), 10, None, "activity")
        return [send], [ShownChange(target.device_id, newest.id, forget_activity_token=False)]
    state = _end_state_for_superseded(target.shown_request_id, 0)
    send = LiveSend(target.device_id, target.activity_token, target.environment, live_end_payload(state, now), 10, None, "activity")
    return [send], [ShownChange(target.device_id, None, forget_activity_token=True)]


def plan_registered(
    target: LiveTarget, important: List[Request], sender: SenderOf, preview: bool, now: float
) -> Tuple[List[LiveSend], List[ShownChange]]:
    """Après un PUT /v1/devices/current/live-activity : si la demande que cet appareil montre
    n'attend plus (ou n'a jamais été une décision importante encore en attente), applique
    aussitôt la règle de fermeture plutôt que d'attendre le prochain changement de demande."""
    if target.shown_request_id is not None and any(r.id == target.shown_request_id for r in important):
        return [], []
    return _close_shown(target, important, sender, preview, now)


def plan_live(
    changed: Request,
    created: bool,
    important: List[Request],
    targets: List[LiveTarget],
    sender: SenderOf,
    preview: bool,
    now: float,
) -> Tuple[List[LiveSend], List[ShownChange]]:
    """`important` : les décisions importantes encore en attente (RequestStore.important_pending(),
    la plus récente d'abord), après ce changement ; `changed` y figure encore si elle vient
    d'être créée, plus du tout si elle vient de se fermer. D1 : seule une demande importante
    déclenche quoi que ce soit ici."""
    if not changed.important:
        return [], []
    if created:
        others = _others_count(important, changed.id)
        expiration = int(changed.expires_at) if changed.expires_at is not None else None
        start_state_sender = sender(changed)
        sends: List[LiveSend] = []
        shown_changes: List[ShownChange] = []
        for target in targets:
            if not target.start_token:
                continue
            payload = live_start_payload(changed, start_state_sender, others, preview, now)
            sends.append(LiveSend(target.device_id, target.start_token, target.environment, payload, 10, expiration, "start"))
            # D3 : la demande devient celle que l'iPhone montre ; l'ancien jeton d'Activité
            # (s'il y en avait un) est oublié, le nouveau arrivera par le prochain PUT.
            shown_changes.append(ShownChange(target.device_id, changed.id, forget_activity_token=True))
            if target.activity_token and target.shown_request_id and target.shown_request_id != changed.id:
                old_state = _end_state_for_superseded(target.shown_request_id, others)
                sends.append(LiveSend(target.device_id, target.activity_token, target.environment, live_end_payload(old_state, now), 10, None, "activity"))
        return sends, shown_changes
    # changed vient de se fermer (répondue ou expirée) : seuls les appareils concernés réagissent.
    sends = []
    shown_changes = []
    for target in targets:
        if target.shown_request_id == changed.id:
            t_sends, t_changes = _close_shown(target, important, sender, preview, now)
            sends += t_sends
            shown_changes += t_changes
        elif target.activity_token and target.shown_request_id is not None:
            shown = next((r for r in important if r.id == target.shown_request_id), None)
            if shown is not None:
                state = live_state(shown, sender(shown), _others_count(important, shown.id), preview)
                sends.append(LiveSend(target.device_id, target.activity_token, target.environment, live_update_payload(state, now), 5, None, "activity"))
    return sends, shown_changes
