"""La section de prompt des tâches planifiées qui livrent à Sheldon (spec 3.1).

Une tâche planifiée tourne comme plateforme « cron », sans mémoire et sans le hint de la
plateforme Sheldon (cron/scheduler.py:358-366, :5773-5777) : cette section, qu'Hermes rend au
début de sa session (ctx.register_system_prompt_section), lui dit ce que Sheldon dessine. Rien
ici n'importe Hermes : la tâche est lue par la fonction que l'appelant passe (adapter.cron_job).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Optional

SECTION_ID = "sheldon-scheduled"
SCHEDULED_TEXT = (
    "This scheduled task delivers to Sheldon, the user's iPhone and Mac app, which draws native visual "
    "blocks. Its first line becomes the title of a card in the user's Fil: a short summary under 60 "
    "characters, with figures. Show figures, lists, objects and steps in ```sheldon blocks (one JSON object "
    "with a `type` per fence), with one or two sentences that make sense without them; load "
    "`sheldon:blocks` with skill_view if this task did not attach it, then the sheet it points to for each "
    "family you use. You have no memory here: the user's preferences for this report live in this task's "
    "prompt. Answer [SILENT] when there is nothing new."
)
_PLATFORM = "sheldon"
# cron/scheduler.py:5115 : f"cron_{job_id}_{AAAAMMJJ_HHMMSS}".
_SESSION = re.compile(r"cron_(?P<job>.+?)_\d{8}_\d{6}")


def job_id_of(session_id: str) -> Optional[str]:
    match = _SESSION.fullmatch(session_id or "")
    return match.group("job") if match else None


def delivers_to_sheldon(job: Mapping[str, Any]) -> bool:
    """La tâche livre-t-elle à Sheldon ? Ses cibles (cron/scheduler.py:2352-2427) : « sheldon »,
    « sheldon:<chat> », « origin » quand elle a été créée dans Sheldon, ou « all » (Sheldon a un
    chat maison, SHELDON_HOME_CHANNEL)."""
    deliver = job.get("deliver")
    if isinstance(deliver, (list, tuple)):
        parts = [str(part) for part in deliver]
    elif isinstance(deliver, str):
        parts = deliver.split(",")
    else:
        return False
    origin = job.get("origin")
    origin_platform = origin.get("platform") if isinstance(origin, Mapping) else None
    for raw in parts:
        part = raw.strip().casefold()
        if part in (_PLATFORM, "all") or part.startswith(_PLATFORM + ":"):
            return True
        if part == "origin" and origin_platform == _PLATFORM:
            return True
    return False


def scheduled_section(
    session_info: Mapping[str, Any],
    find_job: Callable[[str], Optional[Mapping[str, Any]]],
    deliver_platform: Callable[[], str] = lambda: "",
) -> str:
    """Le texte de la section pour cette session, ou "" : hors d'une tâche planifiée, pour une
    tâche qui livre ailleurs, ou sur toute erreur (Hermes écarterait une section qui lève, mais
    une chaîne vide est plus sûre). La tâche relue l'emporte sur la variable de session, qui ne
    porte que la première cible."""
    try:
        if session_info.get("platform") != "cron":
            return ""
        job_id = job_id_of(str(session_info.get("session_id") or ""))
        job = find_job(job_id) if job_id else None
        if job is not None:
            return SCHEDULED_TEXT if delivers_to_sheldon(job) else ""
        return SCHEDULED_TEXT if deliver_platform() == _PLATFORM else ""
    except Exception:
        return ""
