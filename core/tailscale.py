"""Ce que l'extension demande à Tailscale : le nom du Mac mini et l'état de Serve."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Callable, List, Optional
from urllib.parse import urlparse

APP_BINARY = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


class TailscaleError(Exception):
    """Tailscale a répondu par un échec ; detail est la première ligne de sa sortie d'erreur."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def find_binary(
    which: Callable[[str], Optional[str]] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
) -> Optional[str]:
    found = which("tailscale")
    if found:
        return found
    return APP_BINARY if exists(APP_BINARY) else None


def _load(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_dns_name(status_json: str) -> Optional[str]:
    name = str((_load(status_json).get("Self") or {}).get("DNSName") or "").rstrip(".")
    return name or None


def parse_serve_proxy(serve_json: str, host: str, public_port: int) -> Optional[str]:
    web_config = _load(serve_json).get("Web") or {}
    handlers = (web_config.get(f"{host}:{public_port}") or {}).get("Handlers") or {}
    return (handlers.get("/") or {}).get("Proxy")


def proxy_matches(proxy: Optional[str], local_port: int) -> bool:
    if not isinstance(proxy, str) or not proxy:
        return False
    parsed = urlparse(proxy)
    try:
        port = parsed.port
    except ValueError:  # un port hors de 0 à 65535 dans la config : ce n'est pas Sheldon
        return False
    return parsed.hostname in ("127.0.0.1", "localhost") and port == local_port


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def funnel_open(serve_json: str) -> bool:
    """Vrai si Tailscale Funnel ouvre quoi que ce soit à Internet sur ce Mac.

    Sur le Mac mini, jamais de Funnel : toute entrée active de AllowFunnel
    ({"<hôte>:<port>": true}) suffit, quel que soit son port ou sa cible, à la racine de
    ipn.ServeConfig (« tailscale serve status --json ») ou dans une session de Foreground
    (serve lancé sans --bg).
    """
    root = _load(serve_json)
    for config in [root, *(_dict(session) for session in _dict(root.get("Foreground")).values())]:
        if any(allowed is True for allowed in _dict(config.get("AllowFunnel")).values()):
            return True
    return False


def serve_command(binary: str, public_port: int, local_port: int) -> List[str]:
    return [binary, "serve", "--bg", f"--https={public_port}", f"http://127.0.0.1:{local_port}"]


def _run(binary: str, *args: str) -> Optional[str]:
    # None : Tailscale introuvable ou muet. TailscaleError : Tailscale a refusé la commande.
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=5.0, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
        raise TailscaleError(lines[0] if lines else f"exit status {result.returncode}")
    return result.stdout


def probe_host(binary: str) -> Optional[str]:
    output = _run(binary, "status", "--json")
    return parse_dns_name(output) if output else None


def probe_serve_status(binary: str) -> Optional[str]:
    """« tailscale serve status --json » tel quel : Serve et Funnel se lisent dans cette seule
    réponse (deux lectures laissaient une seconde réponse muette dire « Funnel fermé »)."""
    return _run(binary, "serve", "status", "--json")
