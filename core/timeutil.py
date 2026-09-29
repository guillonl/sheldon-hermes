"""Format de date commun à toute l'API : ISO 8601 en UTC, millisecondes, suffixe Z."""
from datetime import datetime, timezone


def iso_utc(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
