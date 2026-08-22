"""Cost-controlled map configuration validation."""
from __future__ import annotations

import pytest

from sheplatform.config import Settings


def test_map_configuration_accepts_leaflet_defaults():
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
    ({"MAPBOX_PUBLIC_TOKEN": "sk.secret"}, "MAPBOX_PUBLIC_TOKEN"),
    ({"MAPBOX_GL_VERSION": "3.27.0"}, "MAPBOX_GL_VERSION"),
    ({"MAPBOX_STYLE_STANDARD": "https://example.test/style"}, "MAPBOX_STYLE_STANDARD"),
    ({"MAP_PROVIDER_WARNING_LOADS": 180000,
      "MAP_PROVIDER_CRITICAL_LOADS": 175000}, "thresholds"),
    ({"MAP_PROVIDER_NONCE_TTL_SECONDS": 59}, "NONCE_TTL"),
    ({"GEOCODER_PROVIDER": "nominatim"}, "GEOCODER_PROVIDER"),
])
def test_invalid_or_unapproved_map_configuration_fails_fast(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Settings(**kwargs)


def test_mapbox_configuration_accepts_reviewed_public_settings():
    configured = Settings(MAP_ENGINE="mapbox", MAPBOX_PUBLIC_TOKEN="pk.test-public")
    assert configured.MAPBOX_GL_VERSION == "3.28.1"
    assert configured.MAP_PROVIDER_MONTHLY_LIMIT == 180000
    assert configured.MAPBOX_STYLE_STANDARD == "mapbox://styles/mapbox/standard"
    assert "pk.test-public" not in repr(configured)
