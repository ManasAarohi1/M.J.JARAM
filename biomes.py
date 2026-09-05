# biomes.py
# Loads a biome catalog from a JSON file and exposes helpers the rest of JARAM can use.

from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

# ---------- path resolution ----------

def _default_candidates() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.environ.get("JARAM_BIOMES_FILE") or "",             # env override
        os.path.join(here, "biomes.json"),                     # next to code
        os.path.join(os.getcwd(), "biomes.json"),              # working dir
    ]

def _user_catalog_path() -> str:
    """
    Writable file holding user-added / user-overridden biomes.
    Kept out of the install dir so it survives updates and works in frozen builds.
    """
    override = os.environ.get("JARAM_CUSTOM_BIOMES_FILE")
    if override:
        return override
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], "JARAM")
    else:
        base = os.path.join(os.path.expanduser("~"), ".jaram")
    return os.path.join(base, "custom_biomes.json")

def load_custom_biomes() -> Dict[str, dict]:
    """Raw user catalog ({} when absent/unreadable). Same shape as biomes.json."""
    try:
        with open(_user_catalog_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _write_custom_biomes(data: Dict[str, dict]) -> None:
    path = _user_catalog_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    load_biomes_catalog()

def save_custom_biome(name, color=None, thumbnail_url="", timer=None) -> str:
    """
    Add a biome, or override a built-in one. Returns the stored (UPPERCASE) name.
    """
    key = str(name or "").strip().upper()
    if not key:
        raise ValueError("Biome name is required.")
    data = {k: v for k, v in load_custom_biomes().items() if str(k).upper() != key}
    data[key] = {
        "color": str(color).strip() if str(color or "").strip() else "0x3BA55D",
        "thumbnail_url": str(thumbnail_url or "").strip(),
        "Timer": _parse_timer_seconds(timer),
    }
    _write_custom_biomes(data)
    return key

def delete_custom_biome(name) -> bool:
    """Drop a user entry. False when the name is built-in only (nothing to remove)."""
    key = str(name or "").strip().upper()
    data = load_custom_biomes()
    kept = {k: v for k, v in data.items() if str(k).upper() != key}
    if len(kept) == len(data):
        return False
    _write_custom_biomes(kept)
    return True

def _parse_color(value) -> int:
    """
    Accepts:
      - int (already)
      - "0xRRGGBB" / "0XRRGGBB" (string)
      - "#RRGGBB" (string)
      - decimal string
    Returns an int usable as a Discord embed color.
    """
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return 0x3BA55D  # fallback green

    s = value.strip()
    try:
        if s.startswith(("0x", "0X")):
            return int(s, 16)
        if s.startswith("#"):
            return int(s[1:], 16)
        # decimal?
        return int(s, 10)
    except Exception:
        return 0x3BA55D

def _parse_timer_seconds(value) -> Optional[int]:
    """
    Accepts "120", 120, "120.0", etc. Returns int seconds or None.
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return int(value)
        s = str(value).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None

# ---------- public API ----------

# in-memory cache
_BIOMES: Dict[str, dict] = {}
_BIOMES_PATH: Optional[str] = None

def load_biomes_catalog(path: Optional[str] = None) -> Dict[str, dict]:
    """
    Load the biome catalog JSON.

    Expected shape:
    {
        "GLITCHED": {"color": "0xbfff00", "thumbnail_url": "https://...", "Timer": "600"},
        ...
    }
    Keys (biome names) are case-insensitive; we store them UPPERCASE.
    """
    global _BIOMES, _BIOMES_PATH

    candidates = []
    if path:
        candidates.append(path)
    candidates.extend([c for c in _default_candidates() if c])

    data = None
    used = None
    for p in candidates:
        try:
            if p and os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    used = p
                    break
        except Exception:
            continue

    # Minimal fallback so UI still renders if file missing
    if not isinstance(data, dict):
        data = {
            "NORMAL": {"color": "0x00ff00", "thumbnail_url": "", "Timer": None},
        }
        used = None

    # user catalog overlays the shipped one: overrides keep their column position,
    # brand-new names are appended
    for cname, cmeta in load_custom_biomes().items():
        if not isinstance(cmeta, dict):
            continue
        existing = next((k for k in data if str(k).upper() == str(cname).upper()), None)
        data[existing or cname] = cmeta

    # normalize
    catalog: Dict[str, dict] = {}
    for name, meta in data.items():
        try:
            upper = str(name).upper()
            color = _parse_color(meta.get("color"))
            thumb = str(meta.get("thumbnail_url") or "").strip()
            timer = _parse_timer_seconds(meta.get("Timer") or meta.get("timer"))

            catalog[upper] = {
                "name": upper,
                "color": color,
                "thumbnail_url": thumb,
                "timer": timer,  # seconds or None
            }
        except Exception:
            continue

    _BIOMES = catalog
    _BIOMES_PATH = used
    return _BIOMES

def biome_names() -> list[str]:
    """Ordered list of biome names as loaded."""
    if not _BIOMES:
        load_biomes_catalog()
    return list(_BIOMES.keys())

def biome_meta(name: str) -> Tuple[int, str]:
    """
    Returns (color_int, thumbnail_url) for the given biome.
    Name lookup is case-insensitive.
    """
    if not _BIOMES:
        load_biomes_catalog()
    key = str(name).upper()
    meta = _BIOMES.get(key, {})
    return int(meta.get("color", 0x3BA55D)), str(meta.get("thumbnail_url", ""))

def biome_duration(name: str) -> Optional[int]:
    """
    Returns the configured duration (seconds) for a biome, or None if unknown.
    """
    if not _BIOMES:
        load_biomes_catalog()
    key = str(name).upper()
    meta = _BIOMES.get(key, {})
    t = meta.get("timer")
    return int(t) if isinstance(t, int) else None
