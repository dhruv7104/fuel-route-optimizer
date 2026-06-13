"""
OSRM routing service.

Uses the free public OSRM demo server (router.project-osrm.org).
Makes exactly ONE API call per route request.

Provides:
- get_route()          → polyline waypoints with cumulative miles
- haversine()          → distance between two lat/lng in miles
- station_route_dist() → project station onto route, return (off_miles, route_miles)
"""
import math
import logging
from typing import Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

OSRM_BASE = "http://router.project-osrm.org/route/v1/driving"
TIMEOUT = 30  # seconds


def get_route(start_lat: float, start_lng: float, end_lat: float, end_lng: float):
    """
    Fetch a driving route from OSRM.

    Returns:
        waypoints: list of dicts {"lat", "lng", "cumulative_miles"}
        total_miles: float

    Raises:
        ValueError if route cannot be found.
        requests.RequestException on network errors.
    """
    url = f"{OSRM_BASE}/{start_lng},{start_lat};{end_lng},{end_lat}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }

    logger.info("Calling OSRM: %s", url)
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"OSRM returned no route: {data.get('code')}")

    route = data["routes"][0]
    coords = route["geometry"]["coordinates"]  # [[lng, lat], ...]
    total_meters = route["distance"]
    total_miles = total_meters / 1609.344

    # Build waypoints list with cumulative mileage
    waypoints = []
    cumulative = 0.0
    prev_lat, prev_lng = None, None

    for lng, lat in coords:
        if prev_lat is not None:
            cumulative += haversine(prev_lat, prev_lng, lat, lng)
        waypoints.append({"lat": lat, "lng": lng, "cumulative_miles": cumulative})
        prev_lat, prev_lng = lat, lng

    logger.info("Route fetched: %.1f miles, %d waypoints", total_miles, len(waypoints))
    return waypoints, total_miles


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in miles."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _point_to_segment_dist(
    p_lat: float, p_lng: float,
    a_lat: float, a_lng: float,
    b_lat: float, b_lng: float,
) -> Tuple[float, float]:
    """
    Distance from point P to line segment A-B.

    Returns:
        (off_route_miles, interpolated_cumulative_miles_offset_from_A)
    Uses flat-earth approximation (fine for short segments).
    """
    dx = b_lng - a_lng
    dy = b_lat - a_lat
    seg_sq = dx * dx + dy * dy

    if seg_sq < 1e-12:
        return haversine(p_lat, p_lng, a_lat, a_lng), 0.0

    t = ((p_lng - a_lng) * dx + (p_lat - a_lat) * dy) / seg_sq
    t = max(0.0, min(1.0, t))

    q_lat = a_lat + t * dy
    q_lng = a_lng + t * dx
    off = haversine(p_lat, p_lng, q_lat, q_lng)
    seg_len = haversine(a_lat, a_lng, b_lat, b_lng)
    return off, t * seg_len


def station_route_dist(
    station_lat: float,
    station_lng: float,
    waypoints: List[Dict],
    max_off_route: float = 15.0,
) -> Optional[Tuple[float, float]]:
    """
    Project a station onto the route polyline.

    Returns:
        (off_route_miles, route_cumulative_miles) if within max_off_route miles
        None otherwise.
    """
    best_off = float("inf")
    best_route_dist = 0.0

    for i in range(len(waypoints) - 1):
        a, b = waypoints[i], waypoints[i + 1]
        off, t_len = _point_to_segment_dist(
            station_lat, station_lng,
            a["lat"], a["lng"],
            b["lat"], b["lng"],
        )
        if off < best_off:
            best_off = off
            best_route_dist = a["cumulative_miles"] + t_len

    if best_off <= max_off_route:
        return best_off, best_route_dist
    return None
