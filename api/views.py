"""
API Views.

POST /api/route/
    Body: {"start": "City, State", "end": "City, State"}
    Returns: route + optimal fuel stops + total cost

GET /api/map/?start=...&end=...
    Returns: Interactive Leaflet.js HTML map
"""
import json
import logging

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.geocoder import geocode
from .services.router import get_route
from .services.station_store import stations_near_route
from .services.optimizer import optimize

logger = logging.getLogger(__name__)


from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

class RouteView(APIView):
    """
    POST /api/route/
    Accepts start and end location strings (e.g. "New York, NY"),
    returns the optimal fuel stop plan.
    """
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        data = request.data
        if not data:
            try:
                data = json.loads(request.body.decode("utf-8"))
            except Exception:
                data = {}



        # 1. Try body data
        start_addr = data.get("start", "")
        end_addr = data.get("end", "")

        # 2. Try query parameters as fallback
        if not start_addr:
            start_addr = request.query_params.get("start", "")
        if not end_addr:
            end_addr = request.query_params.get("end", "")

        if isinstance(start_addr, str):
            start_addr = start_addr.strip()
        else:
            start_addr = ""

        if isinstance(end_addr, str):
            end_addr = end_addr.strip()
        else:
            end_addr = ""

        if not start_addr or not end_addr:
            return Response(
                {"error": "Both 'start' and 'end' fields are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )



        # ── Cache check ───────────────────────────────────────────────────────
        cache_key = f"route:{start_addr.lower()}:{end_addr.lower()}"
        cached = cache.get(cache_key)
        if cached:
            logger.info("Cache hit for %r → %r", start_addr, end_addr)
            return Response(cached)

        # ── Geocode start and end ─────────────────────────────────────────────
        start_coords = geocode(start_addr + ", USA")
        if not start_coords:
            return Response(
                {"error": f"Could not geocode start location: {start_addr!r}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        end_coords = geocode(end_addr + ", USA")
        if not end_coords:
            return Response(
                {"error": f"Could not geocode end location: {end_addr!r}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_lat, start_lng = start_coords
        end_lat, end_lng     = end_coords

        # ── Fetch route (1 OSRM API call) ─────────────────────────────────────
        try:
            waypoints, total_miles = get_route(start_lat, start_lng, end_lat, end_lng)
        except Exception as exc:
            logger.exception("Routing error")
            return Response(
                {"error": f"Routing failed: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # ── Find stations near route ──────────────────────────────────────────
        nearby = stations_near_route(waypoints, max_off_route_miles=15.0)

        # ── Optimize fuel stops ───────────────────────────────────────────────
        fuel_stops, total_cost = optimize(nearby, total_miles)

        if total_cost == float("inf"):
            return Response(
                {
                    "error": (
                        "No feasible route found. The route may be too long with "
                        "insufficient stations. Try a different route."
                    )
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        total_gallons = round(total_miles / 10, 2)

        # ── Build response ────────────────────────────────────────────────────
        # Thin out waypoints for response (every 5th point to keep payload small)
        sampled_coords = [
            [wp["lat"], wp["lng"]] for wp in waypoints[::5]
        ]
        # Always include last point
        if waypoints:
            sampled_coords.append([waypoints[-1]["lat"], waypoints[-1]["lng"]])

        map_url = (
            f"/api/map/?start={start_addr}&end={end_addr}"
        )

        result = {
            "route": {
                "start": {"address": start_addr, "lat": start_lat, "lng": start_lng},
                "end":   {"address": end_addr,   "lat": end_lat,   "lng": end_lng},
                "total_distance_miles": round(total_miles, 1),
                "geometry": sampled_coords,
            },
            "fuel_stops": fuel_stops,
            "summary": {
                "total_fuel_cost_usd": total_cost,
                "total_gallons_needed": total_gallons,
                "num_stops": len(fuel_stops),
                "vehicle_range_miles": 500,
                "vehicle_mpg": 10,
            },
            "map_url": map_url,
        }

        cache.set(cache_key, result, timeout=3600)
        return Response(result)


class MapView(APIView):
    """
    GET /api/map/?start=...&end=...
    Returns an HTML page with an interactive Leaflet.js map.
    """

    def get(self, request):
        start_addr = request.query_params.get("start", "").strip()
        end_addr   = request.query_params.get("end", "").strip()

        if not start_addr or not end_addr:
            return HttpResponse("<h2>Missing start or end parameter.</h2>", status=400)

        # Re-use cached route data if available
        cache_key = f"route:{start_addr.lower()}:{end_addr.lower()}"
        route_data = cache.get(cache_key)

        if not route_data:
            # Trigger calculation
            import requests as req
            import urllib.parse

            payload = {"start": start_addr, "end": end_addr}
            # Internal call – just compute inline
            start_coords = geocode(start_addr + ", USA")
            end_coords   = geocode(end_addr + ", USA")

            if not start_coords or not end_coords:
                return HttpResponse("<h2>Could not geocode locations.</h2>", status=400)

            try:
                waypoints, total_miles = get_route(*start_coords, *end_coords)
            except Exception as exc:
                return HttpResponse(f"<h2>Routing error: {exc}</h2>", status=502)

            nearby = stations_near_route(waypoints)
            fuel_stops, total_cost = optimize(nearby, total_miles)
            sampled_coords = [[wp["lat"], wp["lng"]] for wp in waypoints[::5]]

            route_data = {
                "route": {
                    "start": {"address": start_addr, "lat": start_coords[0], "lng": start_coords[1]},
                    "end":   {"address": end_addr,   "lat": end_coords[0],   "lng": end_coords[1]},
                    "total_distance_miles": round(total_miles, 1),
                    "geometry": sampled_coords,
                },
                "fuel_stops": fuel_stops,
                "summary": {
                    "total_fuel_cost_usd": total_cost,
                    "total_gallons_needed": round(total_miles / 10, 2),
                    "num_stops": len(fuel_stops),
                },
            }
            cache.set(cache_key, route_data, timeout=3600)

        html = _build_map_html(route_data)
        return HttpResponse(html, content_type="text/html")


def _build_map_html(data: dict) -> str:
    route   = data["route"]
    stops   = data["fuel_stops"]
    summary = data["summary"]

    polyline_coords = json.dumps(route["geometry"])
    stops_json      = json.dumps(stops)

    start_lat = route["start"]["lat"]
    start_lng = route["start"]["lng"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Fuel Route Optimizer</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; }}
    #header {{
      background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
      padding: 16px 24px;
      display: flex; align-items: center; justify-content: space-between;
      border-bottom: 1px solid #334155;
    }}
    #header h1 {{ font-size: 1.4rem; color: #38bdf8; }}
    #summary {{
      background: #1e293b;
      padding: 12px 24px;
      display: flex; gap: 32px; flex-wrap: wrap;
      border-bottom: 1px solid #334155;
    }}
    .stat {{ display: flex; flex-direction: column; }}
    .stat-label {{ font-size: 0.7rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
    .stat-value {{ font-size: 1.1rem; font-weight: 700; color: #38bdf8; }}
    #map {{ height: calc(100vh - 110px); }}
    .leaflet-popup-content-wrapper {{
      background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
      border-radius: 8px;
    }}
    .leaflet-popup-tip {{ background: #1e293b; }}
    .popup-title {{ font-weight: 700; color: #38bdf8; font-size: 0.95rem; margin-bottom: 4px; }}
    .popup-row {{ font-size: 0.82rem; color: #94a3b8; margin: 2px 0; }}
    .popup-price {{ color: #4ade80; font-weight: 700; font-size: 1rem; }}
    .popup-cost {{ color: #fb923c; font-weight: 600; }}
  </style>
</head>
<body>
<div id="header">
  <h1>⛽ Fuel Route Optimizer</h1>
  <span style="color:#94a3b8;font-size:0.85rem">{route["start"]["address"]} → {route["end"]["address"]}</span>
</div>
<div id="summary">
  <div class="stat">
    <span class="stat-label">Total Distance</span>
    <span class="stat-value">{route["total_distance_miles"]:,.1f} mi</span>
  </div>
  <div class="stat">
    <span class="stat-label">Fuel Cost</span>
    <span class="stat-value">${summary["total_fuel_cost_usd"]:,.2f}</span>
  </div>
  <div class="stat">
    <span class="stat-label">Gallons Needed</span>
    <span class="stat-value">{summary["total_gallons_needed"]:,.1f} gal</span>
  </div>
  <div class="stat">
    <span class="stat-label">Fuel Stops</span>
    <span class="stat-value">{summary["num_stops"]}</span>
  </div>
</div>
<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const map = L.map('map').setView([{start_lat}, {start_lng}], 5);

L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '© OpenStreetMap © CARTO',
  maxZoom: 19,
}}).addTo(map);

// Route polyline
const routeCoords = {polyline_coords};
const polyline = L.polyline(routeCoords, {{
  color: '#38bdf8', weight: 4, opacity: 0.85,
}}).addTo(map);
map.fitBounds(polyline.getBounds(), {{ padding: [40, 40] }});

// Start / End markers
const startIcon = L.divIcon({{
  html: '<div style="background:#4ade80;border:3px solid #fff;width:16px;height:16px;border-radius:50%;"></div>',
  className: '', iconSize: [16, 16], iconAnchor: [8, 8],
}});
const endIcon = L.divIcon({{
  html: '<div style="background:#f87171;border:3px solid #fff;width:16px;height:16px;border-radius:50%;"></div>',
  className: '', iconSize: [16, 16], iconAnchor: [8, 8],
}});
const fuelIcon = L.divIcon({{
  html: '<div style="background:#fb923c;border:2px solid #fff;width:13px;height:13px;border-radius:50%;font-size:9px;line-height:13px;text-align:center;">⛽</div>',
  className: '', iconSize: [13, 13], iconAnchor: [6, 6],
}});

if (routeCoords.length > 0) {{
  L.marker(routeCoords[0], {{icon: startIcon}})
    .addTo(map)
    .bindPopup('<div class="popup-title">🚀 START</div><div class="popup-row">{route["start"]["address"]}</div>');
  L.marker(routeCoords[routeCoords.length - 1], {{icon: endIcon}})
    .addTo(map)
    .bindPopup('<div class="popup-title">🏁 DESTINATION</div><div class="popup-row">{route["end"]["address"]}</div>');
}}

// Fuel stops
const stops = {stops_json};
stops.forEach((s, i) => {{
  L.marker([s.latitude, s.longitude], {{icon: fuelIcon}})
    .addTo(map)
    .bindPopup(`
      <div class="popup-title">⛽ Stop ${{i+1}}: ${{s.name}}</div>
      <div class="popup-row">${{s.city}}, ${{s.state}}</div>
      <div class="popup-row">${{s.address}}</div>
      <div class="popup-price">$${{s.retail_price.toFixed(3)}}/gal</div>
      <div class="popup-cost">Buy ${{s.fuel_gallons}} gal → $${{s.fuel_cost_usd.toFixed(2)}}</div>
      <div class="popup-row">📍 Mile ${{s.route_distance_from_start_miles.toFixed(0)}} from start</div>
    `);
}});
</script>
</body>
</html>"""
