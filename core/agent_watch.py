"""Les agents (les profils d'Hermes) qui naissent ou disparaissent pendant que le gateway tourne.

Hermes n'annonce pas un profil créé (« hermes profile create »), renommé ou retiré. Le relais de
l'outbox appelle check() à chaque tour ; toutes les 30 s, check() relit la liste des agents, la
compare à la précédente et range dans l'outbox un « agent.upsert » par agent nouveau ou changé et
un « agent.deleted » par agent disparu. Le relais les publie ensuite sur /v1/events, comme le reste.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .conversations import Agent
from .outbox import Outbox

WATCH_EVERY = 30.0


class AgentWatch:
    def __init__(
        self,
        agents: Callable[[], List[Agent]],
        conversation_id: Callable[[Agent], str],
        outbox: Outbox,
        clock: Callable[[], float] = time.time,
        every: float = WATCH_EVERY,
    ) -> None:
        self._agents = agents
        self._conversation_id = conversation_id
        self._outbox = outbox
        self._clock = clock
        self._every = every
        self._last_look: Optional[float] = None
        # Ce que l'app connaît déjà : la forme de GET /v1/agents, par id d'agent.
        self._known: Optional[Dict[str, Dict[str, Any]]] = None

    def check(self) -> int:
        """Le nombre d'événements rangés dans l'outbox (0 entre deux relectures)."""
        now = self._clock()
        if self._last_look is not None and now - self._last_look < self._every:
            return 0
        self._last_look = now
        current = {agent.id: agent.to_json(self._conversation_id(agent)) for agent in self._agents()}
        if self._known is None:
            # Premier tour : l'app lit la liste à sa connexion (GET /v1/agents), rien à annoncer.
            self._known = current
            return 0
        changed = [payload for agent_id, payload in current.items() if self._known.get(agent_id) != payload]
        removed = [agent_id for agent_id in self._known if agent_id not in current]
        for payload in changed:
            self._outbox.append("agent.upsert", {"agent": payload})
        for agent_id in removed:
            self._outbox.append("agent.deleted", {"agentId": agent_id})
        self._known = current
        return len(changed) + len(removed)
