"""The runner's streaming paths reproduce their one-pass forms exactly (2026-10-07).

docs/PERFORMANCE_2026-10-07.md: the Edie bins of ``edges.parquet`` are filled
row group by row group (:class:`microsim.runner._EdieAccumulator`) instead of
from a copy of the whole run's ``(t, x, v)``, and the section rules read the
vehicles on their own roads from a per-step index
(:class:`microsim.runner._RoadIndex`) instead of a pass over every vehicle.
Both are claimed bit-identical; these tests pin the claim on inputs the end-to-end
fingerprints do not reach (ragged chunks, out-of-grid rows, roads in any order).
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd

from microsim.runner import _edie_edges_frame, _EdieAccumulator, _RoadIndex


def _rows(n: int, seed: int = 0) -> pd.DataFrame:
    """Random samples, a few outside the grid on every side, in time order."""
    rng = np.random.default_rng(seed)
    t = np.sort(rng.uniform(-5.0, 650.0, n))
    x = rng.uniform(-50.0, 2150.0, n)
    v = rng.uniform(0.0, 33.0, n) * rng.choice([1.0, 1e-3, 1e3], n)
    return pd.DataFrame({"t": t, "x": x, "v": v})


class TestEdieAccumulator:
    def test_chunked_equals_one_pass_bit_for_bit(self) -> None:
        traj = _rows(20_000)
        whole = _edie_edges_frame(traj, 0.5, 600.0, 2049.0)
        acc = _EdieAccumulator(0.5, 600.0, 2049.0)
        cuts = [0, 1, 7, 4_999, 5_000, 12_345, 19_999, 20_000]
        for lo, hi in pairwise(cuts):
            chunk = traj.iloc[lo:hi]
            acc.add(chunk["t"].to_numpy(), chunk["x"].to_numpy(), chunk["v"].to_numpy())
        chunked = acc.frame()
        assert chunked.equals(whole)
        for col in whole.columns:
            assert whole[col].to_numpy().tobytes() == chunked[col].to_numpy().tobytes()

    def test_no_rows_is_the_empty_grid(self) -> None:
        empty = pd.DataFrame({"t": [], "x": [], "v": []}, dtype=np.float64)
        frame = _EdieAccumulator(0.5, 60.0, 250.0).frame()
        assert frame.equals(_edie_edges_frame(empty, 0.5, 60.0, 250.0))
        assert (frame["density"] == 0.0).all() and frame["mean_speed"].isna().all()


class TestRoadIndex:
    @staticmethod
    def _results(n: int, seed: int = 1) -> dict[str, dict[int, object]]:
        rng = np.random.default_rng(seed)
        roads = ["a", "b", "c", ":j_0", "d"]
        ids = [f"v{k:03d}" for k in rng.permutation(n)]
        return {vid: {0: roads[int(rng.integers(len(roads)))], 1: k} for k, vid in enumerate(ids)}

    def test_on_is_the_filtered_pass_in_results_order(self) -> None:
        results = self._results(300)
        index = _RoadIndex(results, 0)
        for roads in (
            ["a"],
            ["c", "a"],
            {"b", ":j_0", "d"},
            ["zz"],
            [],
            ["d", "zz", "a", "b"],
            ["a", "a", "c"],
        ):
            want = [(vid, res) for vid, res in results.items() if res[0] in set(roads)]
            got = index.on(roads)
            assert [vid for vid, _ in got] == [vid for vid, _ in want]
            assert all(a[1] is b[1] for a, b in zip(got, want, strict=True))

    def test_nothing_is_read_before_the_first_query(self) -> None:
        class Exploding(dict[str, object]):
            def items(self):  # type: ignore[override]
                raise AssertionError("read without a query")

        _RoadIndex(Exploding(), 0)  # constructing reads nothing
