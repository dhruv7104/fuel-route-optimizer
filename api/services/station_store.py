"""
In-memory station store.

Loaded once at Django startup (AppConfig.ready) from the SQLite database.
Provides fast bounding-box pre-filtering for route-proximity queries.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Global in-memory list of geocoded stations
_stations: list[dict] = []
_loaded = False


def load() -> None:
    """Load all geocoded stations from the database into memory."""
    global _stations, _loaded
    if _loaded:
        return

    try:
        from api.models import FuelStation
        qs = FuelStation.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        ).values(
            "opis_id", "name", "address", "city", "state",
            "retail_price", "latitude", "longitude",
        )
        _stations = list(qs)
        _loaded = True
        logger.info("Station store: loaded %d geocoded stations", len(_stations))
    except Exception as exc:
        # Database may not exist yet (first migration). Fail silently.
        logger.warning("Station store not loaded: %s", exc)


def reload() -> None:
    """Force reload (call after load_stations management command)."""
    global _loaded
    _loaded = False
    load()


def stations_near_route(
    waypoints: List[Dict],
    max_off_route_miles: float = 15.0,
) -> List[Dict]:
    """
    Return all geocoded stations within max_off_route_miles of the route polyline.

    Uses a lat/lng bounding box for fast pre-filtering, then exact
    point-to-polyline distance for final selection.

    Each returned station dict gains a 'route_dist' key (miles from start).
    """
    from .router import station_route_dist

    if not _stations:
        logger.warning("Station store is empty. Run: python manage.py load_stations")
        return []

    # ── Bounding box with margin ──────────────────────────────────────────────
    lats = [w["lat"] for w in waypoints]
    lngs = [w["lng"] for w in waypoints]
    # ~1 degree latitude ≈ 69 miles; longitude varies by latitude
    margin_lat = max_off_route_miles / 69.0
    margin_lng = max_off_route_miles / 55.0  # conservative (works for contiguous US)

    min_lat = min(lats) - margin_lat
    max_lat = max(lats) + margin_lat
    min_lng = min(lngs) - margin_lng
    max_lng = max(lngs) + margin_lng

    candidates = [
        s for s in _stations
        if min_lat <= s["latitude"] <= max_lat and min_lng <= s["longitude"] <= max_lng
    ]
    logger.debug("Bounding box: %d candidates from %d total", len(candidates), len(_stations))

    # ── Exact projection onto polyline ────────────────────────────────────────
    result = []
    for s in candidates:
        proj = station_route_dist(s["latitude"], s["longitude"], waypoints, max_off_route_miles)
        if proj is not None:
            off_miles, route_miles = proj
            result.append({
                **s,
                "route_dist": route_miles,
                "off_route_miles": round(off_miles, 2),
            })

    logger.info("Stations near route: %d (from %d candidates)", len(result), len(candidates))
    return result
