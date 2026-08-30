"""Fetch the demo-gallery OSM extracts (M5 corridor gallery, CLAUDE.md §11).

Downloads motorway-only OpenStreetMap extracts for the two gallery corridors
via the Overpass API and stores them under ``data/osm/`` (gitignored — this
script is the versioned, reproducible fetch path, per the repo convention that
payloads are never committed):

* ``i24_nashville.osm`` — I-24 southeast of Nashville, TN, covering the
  I-24 MOTION instrumented testbed area (the corridor the ``i24_replica``
  flagship will eventually model). The gallery scenario uses the westbound
  mainline chain.
* ``us75_dallas.osm`` — US-75 (Central Expressway) in Dallas, TX, a few km
  between roughly Knox/Henderson and Walnut Hill. The gallery scenario uses
  the northbound mainline chain.

The exact Overpass QL query per corridor (bbox order: south, west, north,
east) is ``QUERY_TEMPLATE`` below — ways tagged ``highway=motorway`` or
``highway=motorway_link`` inside the bbox, with their nodes recursed down so
the extract is a self-contained OSM XML document that ``netconvert`` (via
``microsim.networks.osm_import``) accepts directly. Restricting to motorway
classes keeps each extract well under 5 MB (typically < 1 MB).

Mirrors are tried in order with retries; Overpass is community-run and
occasionally rate-limited, so transient failures are expected and handled.

Usage (from the repo root)::

    uv run --no-sync python scripts/fetch_gallery_osm.py

Idempotent: existing files are re-downloaded only with ``--force``.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "osm"

#: Overpass API endpoints, tried in order (primary + community mirror).
OVERPASS_ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

#: The exact Overpass QL query. ``{bbox}`` is "south,west,north,east".
#: ``(._;>;)`` recurses down from the matched ways to their nodes so the
#: extract is self-contained; ``out body`` emits plain OSM XML.
QUERY_TEMPLATE: str = (
    '[out:xml][timeout:120];way["highway"~"^(motorway|motorway_link)$"]({bbox});(._;>;);out body;'
)

#: (filename, bbox as (south, west, north, east)) per gallery corridor.
#: Bboxes are deliberately trimmed tight around the mainline to keep the
#: extracts small (task spec: < ~5 MB each).
CORRIDORS: dict[str, tuple[float, float, float, float]] = {
    # I-24 SE of Nashville around the I-24 MOTION testbed miles.
    "i24_nashville.osm": (36.00, -86.65, 36.09, -86.55),
    # US-75 Central Expressway, Dallas (a few km N of downtown).
    "us75_dallas.osm": (32.80, -96.79, 32.87, -96.74),
}

MAX_BYTES = 5 * 1024 * 1024  # keep each extract under ~5 MB (task spec)


def fetch_one(name: str, bbox: tuple[float, float, float, float], dest: Path) -> None:
    """Download one corridor extract, trying each endpoint with retries."""
    query = QUERY_TEMPLATE.format(bbox=",".join(f"{v}" for v in bbox))
    data = urllib.parse.urlencode({"data": query}).encode()
    last_err: Exception | None = None
    for attempt in range(3):
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                req = urllib.request.Request(
                    endpoint, data=data, headers={"User-Agent": "flowstate-gallery-fetch/1.0"}
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    payload = resp.read()
                if not payload.lstrip().startswith(b"<?xml"):
                    raise RuntimeError(f"{endpoint}: non-XML response ({payload[:80]!r})")
                if len(payload) > MAX_BYTES:
                    raise RuntimeError(
                        f"{name}: extract is {len(payload) / 1e6:.1f} MB > 5 MB — "
                        "trim the bbox in CORRIDORS"
                    )
                dest.write_bytes(payload)
                print(f"  {name}: {len(payload) / 1e3:.0f} kB from {endpoint}")
                return
            except (urllib.error.URLError, TimeoutError, RuntimeError) as err:
                last_err = err
                print(f"  {name}: {endpoint} failed ({err}); retrying", file=sys.stderr)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"all Overpass endpoints failed for {name}: {last_err}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, bbox in CORRIDORS.items():
        dest = OUT_DIR / name
        if dest.is_file() and not args.force:
            print(f"  {name}: exists ({dest.stat().st_size / 1e3:.0f} kB), skipping")
            continue
        fetch_one(name, bbox, dest)


if __name__ == "__main__":
    main()
