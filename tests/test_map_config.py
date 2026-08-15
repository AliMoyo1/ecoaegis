"""Cost-controlled map configuration validation."""
from __future__ import annotations

import pytest

from sheplatform.config import Settings


def test_release_one_accepts_leaflet_defaults():
    configured = Settings()
    assert configured.MAP_ENGINE == "leaflet"
    assert configured.GEOCODER_PROVIDER == "none"


@pytest.mark.parametrize("kwargs,match", [
    ({"MAP_ENGINE": "maplibre"}, "MAP_ENGINE"),
    ({"MAP_MIN_ZOOM": 10, "MAP_DEFAULT_ZOOM": 6, "MAP_MAX_ZOOM": 18}, "zooms"),
    ({"MAP_MAX_FEATURES_PER_LAYER": 99}, "MAP_MAX_FEATURES_PER_LAYER"),
    ({"MAP_REQUEST_DEBOUNCE_MS": 99}, "MAP_REQUEST_DEBOUNCE_MS"),
    ({"MAP_TILE_URL_TEMPLATE": "https://tiles.example/{z}/{x}/{y}.png",
      "MAP_TILE_ATTRIBUTION": ""}, "MAP_TILE_ATTRIBUTION"),
    ({"GEOCODER_PROVIDER": "nominatim"}, "GEOCODER_PROVIDER"),
])
def test_invalid_or_unapproved_map_configuration_fails_fast(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Settings(**kwargs)
