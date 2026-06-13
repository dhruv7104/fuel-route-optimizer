import time
import logging
from typing import Dict, Optional, Tuple
from geopy.geocoders import Nominatim, Photon

logger = logging.getLogger(__name__)

_nominatim = Nominatim(user_agent="fuel_route_optimizer_v1", timeout=10)
_photon = Photon(timeout=10)
_cache: Dict[tuple, Optional[Tuple[float, float]]] = {}


def geocode(query: str) -> Optional[Tuple[float, float]]:
    """
    Geocode any free-text query to (lat, lng).
    Returns None if geocoding fails.
    """
    if query in _cache:
        return _cache[query]

    # Try Nominatim first
    try:
        loc = _nominatim.geocode(query, exactly_one=True)
        if loc:
            result = (loc.latitude, loc.longitude)
            _cache[query] = result
            return result
    except Exception as exc:
        logger.warning("Nominatim geocoding failed for %r: %s. Trying Photon fallback...", query, exc)

    # Fallback to Photon
    try:
        loc = _photon.geocode(query, exactly_one=True)
        if loc:
            result = (loc.latitude, loc.longitude)
            _cache[query] = result
            return result
    except Exception as exc:
        logger.warning("Photon geocoding failed for %r: %s", query, exc)

    _cache[query] = None
    return None


def geocode_city_state(city: str, state: str) -> Optional[Tuple[float, float]]:
    """Geocode a US city+state pair with rate-limit awareness."""
    key = (city.strip().lower(), state.strip().upper())
    if key in _cache:
        return _cache[key]

    query = f"{city.strip()}, {state.strip()}, USA"
    result = geocode(query)
    _cache[key] = result
    time.sleep(1.1)  # Nominatim: max 1 req/sec
    return result

