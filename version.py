# version.py
# Single source of truth for the app version, and the GitHub release check.
#
# Release tags follow upstream's scheme: "2x71", "2x81", sometimes with a
# suffix like "2x51-Refresh". Bump __version__ here and tag the release to
# match; everything else (footers, About dialog, update check) follows.

from __future__ import annotations

import re
from typing import Optional, Tuple

__version__ = "2x81"

# owner/repo whose Releases are checked for updates
UPDATE_REPO = "ManasAarohi1/M.J.JARAM"

# Shown in Discord embed footers and the About dialog.
APP_FOOTER = f"M.J.JARAM JX {__version__}"

_TAG_RE = re.compile(r"^\s*v?(\d+)x(\d+)(.*)$", re.IGNORECASE)


def parse_version(tag) -> Optional[Tuple[int, int, str]]:
    """"2x81" -> (2, 81, ""). Returns None for anything unparseable."""
    m = _TAG_RE.match(str(tag or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), m.group(3).strip().lower()


def is_newer(latest, current: str = __version__) -> bool:
    """True only when `latest` parses AND is strictly newer than `current`.

    Unparseable input means "no update" rather than a false alarm: a bad tag
    should never nag the user.
    ponytail: compares (major, minor) only; a "-Refresh" style suffix breaks
    ties as a plain string, which is enough for this tag scheme.
    """
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    if a[:2] != b[:2]:
        return a[:2] > b[:2]
    return a[2] > b[2]


def latest_release(timeout: float = 6.0) -> Optional[dict]:
    """Newest published release as {"tag", "url", "name"}, or None.

    Never raises: no network, rate limiting, or a repo with no releases yet
    all just mean "nothing to report".
    """
    try:
        import requests

        r = requests.get(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"M.J.JARAM/{__version__}"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        tag = str(data.get("tag_name") or "").strip()
        if not tag:
            return None
        return {
            "tag": tag,
            "name": str(data.get("name") or tag),
            "url": str(data.get("html_url")
                       or f"https://github.com/{UPDATE_REPO}/releases/latest"),
        }
    except Exception:
        return None


def check_for_update(timeout: float = 6.0) -> Optional[dict]:
    """The release dict when a newer version exists, else None."""
    rel = latest_release(timeout=timeout)
    if rel and is_newer(rel["tag"]):
        return rel
    return None


if __name__ == "__main__":
    # self-check: the comparison must not fire on same/older/garbage
    assert parse_version("2x81") == (2, 81, "")
    assert parse_version("v2x81") == (2, 81, "")
    assert parse_version("2x51-Refresh") == (2, 51, "-refresh")
    assert parse_version("nonsense") is None
    assert is_newer("2x81", "2x71")
    assert is_newer("3x00", "2x99")
    assert not is_newer("2x71", "2x71")
    assert not is_newer("2x60", "2x71")
    assert not is_newer("2x9", "2x71")          # numeric, not lexical
    assert is_newer("2x71-Refresh", "2x71")
    assert not is_newer("", "2x71")
    assert not is_newer(None, "2x71")
    assert not is_newer("2x81", "garbage")
    print("version.py self-check OK -", APP_FOOTER)
