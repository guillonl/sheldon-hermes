"""Le projet d'une demande ou d'une carte du Fil (spec 12), sans Hermes.

Dans Hermes, un projet est un dossier nommé, propre à un profil (hermes_cli/projects_db.py :
slug, name, icon, color, et ses dossiers ; project_for_path rend celui du plus long dossier qui
contient un chemin). L'adaptateur le lit ; ici, il est vérifié avant d'aller à l'app. Un champ
mal formé tombe seul ; sans slug ni nom lisibles, pas de projet.
"""
from __future__ import annotations

import colorsys
import re
from typing import Any, Dict, Mapping, Optional

MAX_NAME = 40
MAX_ICON = 8
# hermes_cli/projects_db.py, _SLUG_RE : minuscules, chiffres, tirets et soulignés, sans séparateur en tête.
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_HEX = re.compile(r"#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})")
# Les couleurs du desktop d'Hermes (apps/desktop/src/lib/profile-color.ts) : « hsl(210 68% 58%) » ;
# la forme à virgules aussi.
_HSL = re.compile(r"hsl\(\s*(\d{1,3}(?:\.\d+)?)(?:\s*,\s*|\s+)(\d{1,3}(?:\.\d+)?)%(?:\s*,\s*|\s+)(\d{1,3}(?:\.\d+)?)%\s*\)")


def project_json(raw: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Optional[str]]]:
    """{"slug", "name", "icon", "color"} vérifiés : slug comme ceux d'Hermes, nom d'une ligne
    (MAX_NAME au plus), icône de MAX_ICON caractères au plus (sinon None), couleur en « #RRGGBB »
    (une couleur hsl() d'Hermes y est convertie ; sinon None). Sans slug ni nom : None."""
    if not isinstance(raw, Mapping):
        return None
    slug = raw.get("slug")
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        return None
    name = raw.get("name")
    name = " ".join(name.split())[:MAX_NAME].rstrip() if isinstance(name, str) else ""
    if not name:
        return None
    return {"slug": slug, "name": name, "icon": _icon(raw.get("icon")), "color": _color(raw.get("color"))}


def _icon(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    icon = value.strip()
    return icon if icon and len(icon) <= MAX_ICON and "\n" not in icon else None


def _color(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    hex_match = _HEX.fullmatch(text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        return "#" + digits.upper()
    hsl = _HSL.fullmatch(text)
    if hsl is None:
        return None
    hue, saturation, lightness = (float(part) for part in hsl.groups())
    if hue >= 360 or saturation > 100 or lightness > 100:
        return None
    red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in (red, green, blue))
