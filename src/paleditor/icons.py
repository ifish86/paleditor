"""Fetching item icons from the Palworld wiki.

Coverage is limited and that is inherent, not a bug to be fixed later. The
wiki keys its images on display names; the save keys items on internal ids
like ``Blueprint_AssaultRifle_Default2`` and ``SkillCard_ThrowRock``. There is
no mechanical transformation between the two, so an item can only be resolved
once somebody has given it a display name. Measured against a live catalogue
of 470 items, that was roughly 8%: every hit came from a curated name, and
nothing with a raw id resolved.

So this improves as the ``items`` table is curated, and the UI never depends
on it: an item without an icon falls back to its category colour, which every
item has.

Nothing downloaded here is committed. The artwork belongs to the game's
publisher; it is fetched onto the operator's own machine for their own server,
the same arrangement as the Oodle library.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://palworld.fandom.com/api.php"
USER_AGENT = (
    "paleditor/0.1 (self-hosted Palworld chest browser; "
    "https://github.com/ifish86/paleditor)"
)
# The API takes up to 50 titles per query; stay under it and pause between
# batches. This is somebody else's wiki being asked for a few hundred images.
BATCH = 40
PAUSE_SECONDS = 0.6
MAX_ICON_BYTES = 2 * 1024 * 1024

# Fandom's CDN content-negotiates: a .png URL commonly returns WebP. The
# format is taken from the bytes rather than the URL, and only real image
# types are accepted, so an error page cannot be saved as an icon.
IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),           # checked further below
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

MEDIA_TYPES = {
    "png": "image/png",
    "webp": "image/webp",
    "jpg": "image/jpeg",
    "gif": "image/gif",
}


def image_kind(data: bytes) -> str | None:
    """The image format these bytes actually are, or None."""
    for signature, kind in IMAGE_SIGNATURES:
        if not data.startswith(signature):
            continue
        if kind == "webp":
            # RIFF alone is a container; confirm it is really WebP.
            if len(data) < 12 or data[8:12] != b"WEBP":
                continue
        return kind
    return None


@dataclass
class FetchReport:
    resolved: int = 0
    downloaded: int = 0
    skipped: int = 0
    unresolved: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def readable_name(item_id: str) -> str:
    """A best-effort display name from an internal id.

    Splits camelCase while keeping acronym runs together, which is the same
    shape the wiki's titles take. It is only a guess, which is why an
    unresolved item is reported rather than retried.
    """
    text = item_id
    for prefix in ("BOSS_", "PREDATOR_", "SUMMON_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.replace("_", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def candidate_titles(item_id: str, display_name: str | None) -> list[str]:
    """File titles worth trying, most likely first."""
    names: list[str] = []
    if display_name and display_name != item_id:
        names.append(display_name)
    derived = readable_name(item_id)
    if derived not in names:
        names.append(derived)

    titles: list[str] = []
    for name in names:
        for pattern in (f"{name}.png", f"{name} icon.png", f"{name}_icon.png",
                        f"Icon {name}.png"):
            title = "File:" + pattern[:1].upper() + pattern[1:]
            if title not in titles:
                titles.append(title)
    return titles


def _request(url: str, *, timeout: float = 45.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_ICON_BYTES + 1)


def resolve(titles: list[str], *, pause: float = PAUSE_SECONDS) -> dict[str, str]:
    """Map file titles to image URLs, in batches."""
    found: dict[str, str] = {}
    for start in range(0, len(titles), BATCH):
        chunk = titles[start:start + BATCH]
        query = urllib.parse.urlencode(
            {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            }
        )
        try:
            payload = json.loads(_request(f"{API}?{query}"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            log.warning("icon lookup failed for a batch of %s: %s", len(chunk), exc)
            continue
        for page in payload.get("query", {}).get("pages", {}).values():
            info = page.get("imageinfo")
            if info:
                found[page["title"]] = info[0]["url"]
        if pause:
            time.sleep(pause)
    return found


def fetch(
    conn: sqlite3.Connection,
    destination: Path,
    *,
    only_in_world: bool = True,
    refresh: bool = False,
    pause: float = PAUSE_SECONDS,
) -> FetchReport:
    """Download what can be resolved, and record it against the catalogue."""
    destination.mkdir(parents=True, exist_ok=True)
    report = FetchReport()

    where = "WHERE item_id IN (SELECT DISTINCT item_id FROM slots WHERE item_id IS NOT NULL)"
    rows = conn.execute(
        f"SELECT item_id, display_name, icon FROM items {where if only_in_world else ''} "
        "ORDER BY item_id"
    ).fetchall()

    pending = []
    for row in rows:
        if row["icon"] and not refresh and (destination / row["icon"]).is_file():
            report.skipped += 1
            continue
        pending.append(row)
    if not pending:
        return report

    titles_for: dict[str, list[str]] = {
        row["item_id"]: candidate_titles(row["item_id"], row["display_name"])
        for row in pending
    }
    every_title = list(dict.fromkeys(t for ts in titles_for.values() for t in ts))
    log.info(
        "resolving %s item(s) through %s candidate title(s)",
        len(pending), len(every_title),
    )
    found = resolve(every_title, pause=pause)

    for item_id, titles in titles_for.items():
        url = next((found[t] for t in titles if t in found), None)
        if url is None:
            report.unresolved.append(item_id)
            continue
        report.resolved += 1
        try:
            data = _request(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            report.errors[item_id] = str(exc)
            continue
        if len(data) > MAX_ICON_BYTES:
            report.errors[item_id] = f"icon larger than {MAX_ICON_BYTES} bytes"
            continue
        kind = image_kind(data)
        if kind is None:
            report.errors[item_id] = "downloaded file is not an image"
            continue
        filename = f"{_safe(item_id)}.{kind}"
        (destination / filename).write_bytes(data)
        conn.execute(
            "UPDATE items SET icon = ? WHERE item_id = ?", (filename, item_id)
        )
        report.downloaded += 1
        if pause:
            time.sleep(pause / 2)

    conn.commit()
    return report


def _safe(item_id: str) -> str:
    """A filename that cannot escape the icon directory."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", item_id)
