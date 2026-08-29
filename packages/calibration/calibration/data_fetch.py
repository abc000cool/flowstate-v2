"""Fetch helpers for public calibration datasets (CLAUDE.md §6.2).

Only the NGSIM trajectory data is publicly downloadable without registration;
highD must be requested from leveldXdata (https://levelxdata.com/highd-dataset/)
and I-24 MOTION requires registration at https://i24motion.org — no fetcher
exists for those by design. Note CLAUDE.md §6.2 prefers the Montanino & Punzo
*reconstructed* NGSIM trajectories (distributed via the MULTITUDE project)
over the raw data for calibration; the raw public export fetched here keeps
the same column schema and is what ``loaders.ngsim`` parses either way.

Tests never call these functions (no network in tests); they exist for the
manual onboarding path and the CLI.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path
from typing import Final

NGSIM_DATASET_ID: Final[str] = "8ect-6jqj"
"""data.transportation.gov dataset id: 'Next Generation Simulation (NGSIM)
Vehicle Trajectories and Supporting Data'."""

NGSIM_ROWS_CSV_URL: Final[str] = (
    f"https://data.transportation.gov/api/views/{NGSIM_DATASET_ID}/rows.csv?accessType=DOWNLOAD"
)
"""Full-dataset CSV export (~multi-GB: I-80 + US-101 + Lankershim + Peachtree)."""

NGSIM_SODA_CSV_URL: Final[str] = f"https://data.transportation.gov/resource/{NGSIM_DATASET_ID}.csv"
"""Socrata SODA endpoint — supports ``$limit`` / ``$where`` for subsets."""


def ngsim_subset_url(*, location: str | None = "i-80", limit: int | None = None) -> str:
    """Build a SODA query URL for an NGSIM subset.

    Args:
        location: NGSIM ``location`` field filter (``"i-80"``, ``"us-101"``,
            ``"lankershim"``, ``"peachtree"``); None for all locations.
        limit: Optional ``$limit`` row cap (SODA default is 1000, so pass an
            explicit large value for real pulls).

    Returns:
        Fully encoded URL string.
    """
    params: dict[str, str] = {}
    if location is not None:
        params["$where"] = f"location='{location}'"
    if limit is not None:
        if limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")
        params["$limit"] = str(limit)
    query = urllib.parse.urlencode(params)
    return NGSIM_SODA_CSV_URL + (f"?{query}" if query else "")


def fetch_ngsim_i80(
    dest: str | Path,
    *,
    url: str | None = None,
    limit: int | None = None,
    timeout_s: float = 120.0,
    chunk_bytes: int = 1 << 20,
) -> Path:
    """Download the public NGSIM I-80 trajectory CSV subset.

    Streams the Socrata SODA CSV export filtered to ``location='i-80'`` (or a
    caller-supplied URL) to ``dest``. **Never called from tests** — network
    access is a deliberate manual step; the download lands under the
    gitignored ``data/`` tree by convention.

    Args:
        dest: Destination file path (parent directories are created).
        url: Override the download URL entirely (e.g.
            :data:`NGSIM_ROWS_CSV_URL` for the full dataset).
        limit: Optional row cap for a quick subset (SODA ``$limit``).
        timeout_s: Socket timeout [s].
        chunk_bytes: Streaming chunk size.

    Returns:
        The destination path.
    """
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    final_url = url if url is not None else ngsim_subset_url(location="i-80", limit=limit)
    with urllib.request.urlopen(final_url, timeout=timeout_s) as resp:
        with target.open("wb") as fh:
            while True:
                chunk = resp.read(chunk_bytes)
                if not chunk:
                    break
                fh.write(chunk)
    return target
