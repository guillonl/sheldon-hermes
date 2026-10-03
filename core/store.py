"""Ce que l'extension garde sur le Mac mini : identifiant, codes, appareils.

Aucune clé n'est gardée en clair, seulement son empreinte SHA-256. La base est
partagée entre le gateway d'Hermes et la commande « hermes sheldon » (deux
processus) : mode WAL et délai d'attente sur verrou.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .sqlite import ensure_columns, open_database

CLIENT_MESSAGE_RETENTION_SECONDS = 7 * 86400
# La fenêtre du plafond d'offres de l'outil sheldon_pair.
OFFER_WINDOW_SECONDS = 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS server (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    id TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS pairing_offers (
    code_hash TEXT PRIMARY KEY,
    by_tool INTEGER NOT NULL,
    created_at REAL NOT NULL,
    image TEXT
);
CREATE TABLE IF NOT EXISTS owner (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    token_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_seen_at REAL
);
CREATE TABLE IF NOT EXISTS client_messages (
    client_message_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_tokens (
    device_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    environment TEXT NOT NULL,
    voip_token TEXT,
    calls_allowed INTEGER NOT NULL DEFAULT 1,
    quiet_hours TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_key (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    key BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS live_activities (
    device_id TEXT PRIMARY KEY,
    environment TEXT NOT NULL,
    start_token TEXT,
    activity_token TEXT,
    shown_request_id TEXT,
    updated_at REAL NOT NULL
);
"""

_DEVICE_COLUMNS = "id, name, platform, created_at, last_seen_at"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    platform: str
    created_at: float
    last_seen_at: Optional[float]


@dataclass(frozen=True)
class LiveTarget:
    """Une Activité en direct des décisions importantes, par appareil iOS (spec du 2 octobre 2026)."""

    device_id: str
    environment: str
    start_token: Optional[str]
    activity_token: Optional[str]
    shown_request_id: Optional[str]


@dataclass(frozen=True)
class PushTarget:
    device_id: str
    token: str
    environment: str
    # « Hermes t'appelle » (core/calls.py) : jeton PushKit de l'iPhone et réglages de l'appareil.
    voip_token: Optional[str] = None
    calls_allowed: bool = True
    quiet_hours: Optional[Dict[str, str]] = None


def _quiet_hours(raw: Optional[str]) -> Optional[Dict[str, str]]:
    """Les heures calmes gardées en JSON. Illisibles (base modifiée à la main) : {} , que
    in_quiet_hours (core/calls.py) compte comme « l'appareil dort », sans faire tomber les pushes."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _device(row: Optional[sqlite3.Row]) -> Optional[Device]:
    if row is None:
        return None
    return Device(row["id"], row["name"], row["platform"], row["created_at"], row["last_seen_at"])


class DeviceStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        # L'identifiant du serveur ne change jamais une fois écrit : lu une fois, gardé ici.
        self._server_id: Optional[str] = None
        self._conn = open_database(self._path, _SCHEMA)
        # Spec 3.2 : la version du catalogue des blocs que dessine chaque appareil (context.catalog).
        ensure_columns(self._conn, "devices", {"catalog": "INTEGER"})

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def server_id(self) -> str:
        with self._lock:
            if self._server_id is None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO server (slot, id, created_at) VALUES (1, ?, ?)",
                    (uuid.uuid4().hex, self._clock()),
                )
                self._server_id = self._conn.execute("SELECT id FROM server WHERE slot = 1").fetchone()["id"]
            return self._server_id

    def push_key(self) -> bytes:
        """La clé des empreintes opaques des notifications sans aperçu (décision A54) :
        32 octets aléatoires, créés une seule fois puis gardés tels quels, jamais
        journalisés. Donnée à l'app dans la réponse d'appairage (base64url) : elle
        recalcule la même empreinte HMAC pour retrouver la conversation visée, sans
        qu'un identifiant lisible ne quitte jamais le Mac mini."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO push_key (slot, key, created_at) VALUES (1, ?, ?)",
                (secrets.token_bytes(32), self._clock()),
            )
            row = self._conn.execute("SELECT key FROM push_key WHERE slot = 1").fetchone()
            return bytes(row["key"])

    def _tool_offer_count(self, now: float) -> int:
        """Les offres plafonnées (l'outil sheldon_pair ou /v1/pair/offers) de la dernière heure."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM pairing_offers WHERE by_tool = 1 AND created_at > ?",
            (now - OFFER_WINDOW_SECONDS,),
        ).fetchone()[0]

    def pairing_offer_limit_reached(self, tool_limit: int) -> bool:
        """Vrai si le plafond horaire est déjà atteint, sans créer d'offre : à vérifier avant de
        sonder Tailscale, pour qu'une requête refusée n'en paie jamais le prix (tâche 18,
        relecture m2). add_pairing_code reste le contrôle qui compte : celui-ci ne fait
        qu'éviter, dans le cas courant, une sonde qu'on sait déjà perdue d'avance."""
        with self._lock:
            return self._tool_offer_count(self._clock()) >= tool_limit

    def add_pairing_code(self, code: str, ttl_seconds: float, tool_limit: Optional[int] = None) -> Optional[float]:
        """Range un nouveau code. Une seule offre à la fois : la nouvelle annule toutes les autres.

        tool_limit : une offre plafonnée (l'outil sheldon_pair ou la route /v1/pair/offers,
        qui partagent ce même compteur horaire), refusée (None) quand le plafond de l'heure est
        déjà atteint. La commande (tool_limit None) n'est ni limitée ni comptée : un agent ne
        peut pas empêcher l'utilisateur de relier un appareil.
        """
        now = self._clock()
        expires_at = now + ttl_seconds
        code_hash = hash_secret(code)
        with self._lock:
            # Le compte et l'écriture dans une même transaction : le gateway, la commande et les
            # outils (d'autres processus) ne dépassent pas la limite à plusieurs.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if tool_limit is not None:
                    if self._tool_offer_count(now) >= tool_limit:
                        self._conn.execute("ROLLBACK")
                        return None
                self._conn.execute("DELETE FROM pairing_codes")
                self._conn.execute(
                    "DELETE FROM pairing_offers WHERE created_at <= ? AND image IS NULL", (now - OFFER_WINDOW_SECONDS,)
                )
                self._conn.execute(
                    "INSERT INTO pairing_codes (code_hash, expires_at) VALUES (?, ?)", (code_hash, expires_at)
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO pairing_offers (code_hash, by_tool, created_at) VALUES (?, ?, ?)",
                    (code_hash, int(tool_limit is not None), now),
                )
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
        return expires_at

    def set_pair_image(self, code: str, image: str) -> None:
        """Le QR code en PNG de cette offre, à effacer quand elle ne servira plus."""
        with self._lock:
            self._conn.execute(
                "UPDATE pairing_offers SET image = ? WHERE code_hash = ?", (image, hash_secret(code))
            )

    def take_stale_pair_images(self) -> List[str]:
        """Les QR codes des offres qui ne servent plus (utilisées, expirées ou annulées), oubliés ici."""
        now = self._clock()
        with self._lock:
            rows = self._conn.execute(
                "SELECT code_hash, image FROM pairing_offers o WHERE image IS NOT NULL AND NOT EXISTS ("
                "SELECT 1 FROM pairing_codes c WHERE c.code_hash = o.code_hash AND c.used_at IS NULL "
                "AND c.expires_at > ?)",
                (now,),
            ).fetchall()
            for row in rows:
                self._conn.execute("UPDATE pairing_offers SET image = NULL WHERE code_hash = ?", (row["code_hash"],))
        return [row["image"] for row in rows]

    def consume_pairing_code(self, code: str) -> bool:
        now = self._clock()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE pairing_codes SET used_at = ? "
                "WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                (now, hash_secret(code), now),
            )
            return cursor.rowcount == 1

    def set_owner_token(self, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO owner (slot, token_hash, created_at) VALUES (1, ?, ?) "
                "ON CONFLICT(slot) DO UPDATE SET token_hash = excluded.token_hash, "
                "created_at = excluded.created_at",
                (hash_secret(token), self._clock()),
            )

    def owner_token_matches(self, token: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT token_hash FROM owner WHERE slot = 1").fetchone()
        return row is not None and hmac.compare_digest(row["token_hash"], hash_secret(token))

    def add_device(self, name: str, platform: str, token: str) -> Device:
        device = Device(uuid.uuid4().hex, name, platform, self._clock(), None)
        with self._lock:
            self._conn.execute(
                "INSERT INTO devices (id, name, platform, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (device.id, device.name, device.platform, hash_secret(token), device.created_at),
            )
        return device

    def device_for_token(self, token: str) -> Optional[Device]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE token_hash = ?", (hash_secret(token),)
            ).fetchone()
        return _device(row)

    def device_by_id(self, device_id: str) -> Optional[Device]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
        return _device(row)

    def touch_device(self, device_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE id = ?", (self._clock(), device_id)
            )

    def set_catalog(self, device_id: str, version: int) -> None:
        """La version du catalogue des blocs que cet appareil dit dessiner (spec 3.2)."""
        with self._lock:
            self._conn.execute("UPDATE devices SET catalog = ? WHERE id = ?", (version, device_id))

    def oldest_catalog(self) -> Optional[int]:
        """Le plus petit catalogue des appareils qui l'ont dit ; None si aucun ne l'a dit. Un
        appareil qui ne l'a jamais envoyé (une app d'avant blockNotes) est ignoré : le compter
        priverait aussi l'appareil à jour des nouveaux blocs (spec 3.2)."""
        with self._lock:
            row = self._conn.execute("SELECT MIN(catalog) AS oldest FROM devices WHERE catalog IS NOT NULL").fetchone()
        return int(row["oldest"]) if row is not None and row["oldest"] is not None else None

    def list_devices(self) -> List[Device]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices ORDER BY created_at, id"
            ).fetchall()
        return [_device(row) for row in rows]

    def revoke_device(self, device_id: str) -> bool:
        """Retire un appareil (route ou commande hermes sheldon revoke).

        Un retrait annule aussi toute offre active, quelle que soit son origine (outil, commande
        ou route) : c'est le geste d'alarme de l'utilisateur, et refaire un QR code coûte peu. Sans ce
        ménage, quelqu'un qui tient encore une clé et le tailnet garderait toujours une longueur
        d'avance (chaque appareil relié rouvrant aussitôt l'offre suivante) sur un retrait qui ne
        chasserait plus personne (tâche 18, relecture I2). Le PNG de l'outil, périmé, est effacé
        par discard_stale_images ou par son minuteur.
        """
        with self._lock:
            self._conn.execute("DELETE FROM push_tokens WHERE device_id = ?", (device_id,))
            self._conn.execute("DELETE FROM live_activities WHERE device_id = ?", (device_id,))
            removed = self._conn.execute("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount == 1
            if removed:
                self._conn.execute("DELETE FROM pairing_codes WHERE used_at IS NULL")
            return removed

    def set_push_token(
        self,
        device_id: str,
        token: str,
        environment: str,
        voip_token: Optional[str] = None,
        calls_allowed: bool = True,
        quiet_hours: Optional[Dict[str, str]] = None,
    ) -> None:
        with self._lock:
            # Un même jeton APNs sous un autre appareil (app réinstallée, appareil relié à nouveau) :
            # l'ancien enregistrement ne doit pas doubler les notifications.
            self._conn.execute("DELETE FROM push_tokens WHERE token = ? AND device_id != ?", (token, device_id))
            if voip_token:
                self._conn.execute(
                    "UPDATE push_tokens SET voip_token = NULL WHERE voip_token = ? AND device_id != ?", (voip_token, device_id)
                )
            self._conn.execute(
                "INSERT INTO push_tokens (device_id, token, environment, voip_token, calls_allowed, quiet_hours, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET token = excluded.token, environment = excluded.environment, "
                "voip_token = excluded.voip_token, calls_allowed = excluded.calls_allowed, "
                "quiet_hours = excluded.quiet_hours, updated_at = excluded.updated_at",
                (
                    device_id, token, environment, voip_token, int(calls_allowed),
                    json.dumps(quiet_hours) if quiet_hours is not None else None, self._clock(),
                ),
            )

    def clear_voip_token(self, device_id: str, token: str) -> None:
        """Oublie le jeton PushKit refusé par Apple, s'il n'a pas changé entre-temps ; le jeton APNs reste."""
        with self._lock:
            self._conn.execute(
                "UPDATE push_tokens SET voip_token = NULL WHERE device_id = ? AND voip_token = ?", (device_id, token)
            )

    def clear_push_token(self, device_id: str, token: Optional[str] = None) -> None:
        """Retire le jeton de l'appareil ; avec token, seulement s'il n'a pas changé entre-temps."""
        with self._lock:
            if token is None:
                self._conn.execute("DELETE FROM push_tokens WHERE device_id = ?", (device_id,))
            else:
                self._conn.execute("DELETE FROM push_tokens WHERE device_id = ? AND token = ?", (device_id, token))

    def push_target(self, device_id: str) -> Optional[PushTarget]:
        return next((t for t in self.push_targets() if t.device_id == device_id), None)

    def push_targets(self) -> List[PushTarget]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.device_id, p.token, p.environment, p.voip_token, p.calls_allowed, p.quiet_hours FROM push_tokens p "
                "JOIN devices d ON d.id = p.device_id ORDER BY p.updated_at, p.device_id"
            ).fetchall()
        return [
            PushTarget(
                r["device_id"], r["token"], r["environment"], r["voip_token"], bool(r["calls_allowed"]),
                _quiet_hours(r["quiet_hours"]),
            )
            for r in rows
        ]

    def set_live_activity(
        self,
        device_id: str,
        environment: str,
        start_token: Optional[str],
        activity_token: Optional[str],
        request_id: Optional[str],
    ) -> None:
        """`PUT /v1/devices/current/live-activity` : remplace tout, comme set_push_token.

        Un `start_token` ou `activity_token` repris par un autre appareil (app réinstallée,
        appareil relié à nouveau) quitte l'ancien, pour la même raison que set_push_token."""
        with self._lock:
            if start_token:
                self._conn.execute(
                    "UPDATE live_activities SET start_token = NULL WHERE start_token = ? AND device_id != ?",
                    (start_token, device_id),
                )
            if activity_token:
                self._conn.execute(
                    "UPDATE live_activities SET activity_token = NULL WHERE activity_token = ? AND device_id != ?",
                    (activity_token, device_id),
                )
            self._conn.execute(
                "INSERT INTO live_activities (device_id, environment, start_token, activity_token, "
                "shown_request_id, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET environment = excluded.environment, "
                "start_token = excluded.start_token, activity_token = excluded.activity_token, "
                "shown_request_id = excluded.shown_request_id, updated_at = excluded.updated_at",
                (device_id, environment, start_token, activity_token, request_id, self._clock()),
            )

    def clear_live_activity(self, device_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM live_activities WHERE device_id = ?", (device_id,))

    def live_target(self, device_id: str) -> Optional[LiveTarget]:
        return next((t for t in self.live_targets() if t.device_id == device_id), None)

    def live_targets(self) -> List[LiveTarget]:
        """Les appareils iOS reliés qui ont une ligne live_activities (jamais le Mac : F10)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT l.device_id, l.environment, l.start_token, l.activity_token, l.shown_request_id "
                "FROM live_activities l JOIN devices d ON d.id = l.device_id WHERE d.platform = 'ios' "
                "ORDER BY l.updated_at, l.device_id"
            ).fetchall()
        return [
            LiveTarget(r["device_id"], r["environment"], r["start_token"], r["activity_token"], r["shown_request_id"])
            for r in rows
        ]

    def set_live_shown(self, device_id: str, request_id: Optional[str], forget_activity_token: bool) -> None:
        """La demande que l'Activité de cet appareil montre désormais ; forget_activity_token
        efface aussi son jeton (une Activité qui vient de finir, D3/D4 de la spec)."""
        with self._lock:
            if forget_activity_token:
                self._conn.execute(
                    "UPDATE live_activities SET shown_request_id = ?, activity_token = NULL WHERE device_id = ?",
                    (request_id, device_id),
                )
            else:
                self._conn.execute(
                    "UPDATE live_activities SET shown_request_id = ? WHERE device_id = ?", (request_id, device_id)
                )

    def clear_live_token(self, device_id: str, token: str) -> None:
        """Oublie un jeton de démarrage ou d'Activité refusé par Apple, s'il n'a pas changé
        entre-temps ; l'autre colonne n'est jamais touchée."""
        with self._lock:
            self._conn.execute(
                "UPDATE live_activities SET start_token = NULL WHERE device_id = ? AND start_token = ?",
                (device_id, token),
            )
            self._conn.execute(
                "UPDATE live_activities SET activity_token = NULL WHERE device_id = ? AND activity_token = ?",
                (device_id, token),
            )

    def remember_client_message(self, client_message_id: str, device_id: str) -> bool:
        now = self._clock()
        with self._lock:
            self._conn.execute(
                "DELETE FROM client_messages WHERE created_at < ?",
                (now - CLIENT_MESSAGE_RETENTION_SECONDS,),
            )
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO client_messages (client_message_id, device_id, created_at) "
                "VALUES (?, ?, ?)",
                (client_message_id, device_id, now),
            )
            return cursor.rowcount == 1

    def has_client_messages(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM client_messages LIMIT 1").fetchone() is not None

    def forget_client_message(self, client_message_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM client_messages WHERE client_message_id = ?", (client_message_id,)
            )

    def reset_all(self) -> None:
        with self._lock:
            for table in ("owner", "devices", "pairing_codes", "client_messages", "push_tokens", "live_activities"):
                self._conn.execute(f"DELETE FROM {table}")
