"""Le nom du Mac que l'app affiche (Réglages) : scutil sous macOS, sinon le nom d'hôte réseau.

M2 (revue du branchement à Hermes) : `socket.gethostname()` seul rend une adresse MAC sur un
Mac sans nom d'hôte réseau réglé. `scutil --get ComputerName` lit le nom que Léo voit dans
Réglages Système > Général > Partage (ex. « MacBook Pro de Léo »), celui que Finder et
AirDrop utilisent.
"""
from __future__ import annotations

import socket
import subprocess
import sys
from typing import Callable, Optional


def _scutil_name() -> Optional[str]:
    # Écrit en clair, comme dans tailscale.py : le scanner de plugins d'Hermes doit voir l'appel.
    try:
        result = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=2.0, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def display_name(
    is_macos: bool = sys.platform == "darwin",
    hostname: Callable[[], str] = socket.gethostname,
) -> str:
    """Sous macOS, le nom de scutil s'il répond ; sinon (ou ailleurs) le nom d'hôte réseau,
    tronqué au premier point (« mac-mini.local » -> « mac-mini »)."""
    if is_macos:
        name = _scutil_name()
        if name:
            return name
    return hostname().split(".")[0]
