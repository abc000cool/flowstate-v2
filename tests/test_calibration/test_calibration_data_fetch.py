"""URL-construction tests for calibration.data_fetch (never touches the network)."""

from __future__ import annotations

import pytest

from calibration.data_fetch import (
    NGSIM_DATASET_ID,
    NGSIM_ROWS_CSV_URL,
    NGSIM_SODA_CSV_URL,
    ngsim_subset_url,
)


class TestNgsimUrls:
    def test_documented_dataset_id(self) -> None:
        assert NGSIM_DATASET_ID == "8ect-6jqj"
        assert NGSIM_DATASET_ID in NGSIM_ROWS_CSV_URL
        assert NGSIM_DATASET_ID in NGSIM_SODA_CSV_URL
        assert NGSIM_ROWS_CSV_URL.startswith("https://data.transportation.gov/")

    def test_subset_url_encodes_location_and_limit(self) -> None:
        url = ngsim_subset_url(location="i-80", limit=1000)
        assert url.startswith(NGSIM_SODA_CSV_URL + "?")
        assert "%24where=location%3D%27i-80%27" in url
        assert "%24limit=1000" in url

    def test_subset_url_no_filters(self) -> None:
        assert ngsim_subset_url(location=None) == NGSIM_SODA_CSV_URL

    def test_bad_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            ngsim_subset_url(limit=0)
