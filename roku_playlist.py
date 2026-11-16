#!/usr/bin/env python3
"""Generate a Roku M3U playlist and EPG (XML.GZ) from the i.mjh.nz feeds."""

from __future__ import annotations

import gzip
import json
import logging
import random
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

# --- Configuration ---
OUTPUT_DIR = Path("playlists")
M3U_FILE = OUTPUT_DIR / "roku_all.m3u"
EPG_FILE = OUTPUT_DIR / "roku_all.xml.gz"

CHANNELS_URL = "https://i.mjh.nz/Roku/.channels.json"
EPG_URLS = (
    "https://i.mjh.nz/Roku/all.xml.gz",
    "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Roku/all.xml.gz",
)
STREAM_URL = "https://jmp2.uk/rok-{id}.m3u8"
# Used inside the M3U header; point this at wherever you host the generated EPG.
EPG_REF = "roku_all.xml.gz"

# Every channel is placed in this single category
GROUP_NAME = "Fast Roku"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("roku")


# --- Helpers ---

def fetch(url: str, retries: int = 3) -> bytes | None:
    """GET a URL with retry/backoff. Returns raw bytes or None."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                time.sleep(attempt * 10 + random.uniform(0, 5))
                continue
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            logger.warning("Fetch failed (%s, attempt %d/%d): %s", url, attempt, retries, exc)
            if attempt < retries:
                time.sleep(5)
    return None


def clean(value: str) -> str:
    """Make a value safe for use inside an EXTINF attribute."""
    return str(value).replace('"', "'")


def format_extinf(channel_id: str, chno, name: str, logo: str, group: str) -> str:
    chno_str = str(chno) if chno and str(chno).isdigit() else ""
    return (
        f'#EXTINF:-1 channel-id="{channel_id}" tvg-id="{channel_id}" tvg-chno="{chno_str}" '
        f'tvg-name="{clean(name)}" tvg-logo="{logo}" group-title="{clean(group)}",'
        f'{name.replace(",", "")}\n'
    )


# --- Playlist ---

def build_m3u() -> bool:
    raw = fetch(CHANNELS_URL)
    if not raw:
        logger.error("Could not download Roku channel list")
        return False

    try:
        channels = json.loads(raw.decode("utf-8")).get("channels", {})
    except (ValueError, UnicodeDecodeError) as exc:
        logger.error("Invalid channel JSON: %s", exc)
        return False

    if not channels:
        logger.error("Channel list is empty; keeping previous playlist")
        return False

    lines = [f'#EXTM3U url-tvg="{EPG_REF}"\n']
    for c_id, ch in sorted(channels.items(), key=lambda x: x[1].get("name", "").lower()):
        lines.append(
            format_extinf(c_id, ch.get("chno"), ch.get("name", c_id), ch.get("logo", ""), GROUP_NAME)
        )
        lines.append(STREAM_URL.format(id=c_id) + "\n")

    M3U_FILE.write_text("".join(lines), encoding="utf-8")
    logger.info("Wrote %s (%d channels)", M3U_FILE, len(channels))
    return True


# --- EPG ---

def build_epg() -> bool:
    for url in EPG_URLS:
        data = fetch(url)
        if not data:
            continue
        try:
            try:
                xml_bytes = gzip.decompress(data)
            except gzip.BadGzipFile:
                xml_bytes = data  # served already decompressed
            root = ET.fromstring(xml_bytes)  # validate it's real XML
        except ET.ParseError as exc:
            logger.warning("EPG from %s is not valid XML: %s", url, exc)
            continue

        if root.tag != "tv":
            logger.warning("EPG from %s has unexpected root <%s>", url, root.tag)
            continue

        EPG_FILE.write_bytes(gzip.compress(xml_bytes, compresslevel=9, mtime=0))
        logger.info(
            "Wrote %s (%d channels, %d programmes)",
            EPG_FILE, len(root.findall("channel")), len(root.findall("programme")),
        )
        return True

    logger.error("Could not download a valid EPG")
    return False


# --- Main ---

def main() -> int:
    OUTPUT_DIR.mkdir(exist_ok=True)
    # Only touch Roku outputs so a failed run never leaves the folder empty
    ok_m3u = build_m3u()
    ok_epg = build_epg()
    return 0 if (ok_m3u and ok_epg) else 1


if __name__ == "__main__":
    raise SystemExit(main())
