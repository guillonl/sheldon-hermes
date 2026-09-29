"""Démarrage et arrêt du serveur de la porte d'entrée, sur le Mac mini seulement."""
from __future__ import annotations

from typing import Optional

from aiohttp import web


class ApiServer:
    def __init__(self, app: web.Application, host: str = "127.0.0.1", port: int = 8787) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        if self._runner is not None and self._runner.addresses:
            return self._runner.addresses[0][1]
        return self._port

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        try:
            await self._site.start()
        except OSError:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
