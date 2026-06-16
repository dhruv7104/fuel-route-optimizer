# Fuel Route Optimizer API

A Django REST API that finds the **optimal (cheapest) fuel stops** for a road trip anywhere within the USA.

Built for the Remote Backend Django Engineer assessment.

---

## Features

| Feature | Detail |
|---|---|
| **Route API** | `POST /api/route/` — returns route + fuel stops + total cost (accepts JSON body, form-data, or URL query parameters) |
| **Map View** | `GET /api/map/` — interactive Leaflet.js map in the browser |
| **Routing** | OSRM public API — **1 HTTP call** per route |
| **Geocoding** | Nominatim + **Photon fallback** (bypasses 429 rate limit errors automatically) |
| **CORS Enabled** | Native CORS headers support (`django-cors-headers`) for Hoppscotch testing |
| **Algorithm** | O(N²) Dijkstra Graph Optimization — guaranteed cheapest stop sequence |
| **Data** | 6,700+ US truck stop prices from provided CSV (fully geocoded database) |
| **Cache** | 1-hour in-memory cache for repeated routes |
| **Speed** | Instant on cache hits, < 1.5 seconds on new paths (stations preloaded in memory) |

---

## Quick Start

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up database
python manage.py migrate

# 4. Load fuel stations + geocode (first-time setup — takes ~30 min for full dataset)
python manage.py load_stations

# For a quick test with 200 city/state combos (~5 min):
# python manage.py load_stations --batch 200

# 5. Start server
python manage.py runserver
```

---

## API Reference

### `POST /api/route/`

**Request:**
```json
{
  "start": "New York, NY",
  "end":   "Los Angeles, CA"
}
```

**Response:**
```json
{
  "route": {
    "start": {"address": "New York, NY", "lat": 40.71, "lng": -74.00},
    "end":   {"address": "Los Angeles, CA", "lat": 34.05, "lng": -118.24},
    "total_distance_miles": 2791.2,
    "geometry": [[40.71, -74.00], ...]
  },
  "fuel_stops": [
    {
      "name": "LOVES TRAVEL STOP #450",
      "city": "West Memphis",
      "state": "AR",
      "address": "I-40, EXIT 280 & I-55, EXIT 4",
      "retail_price": 3.32,
      "latitude": 35.14,
      "longitude": -90.18,
      "route_distance_from_start_miles": 1142.3,
      "fuel_gallons": 47.1,
      "fuel_cost_usd": 156.40
    }
  ],
  "summary": {
    "total_fuel_cost_usd": 834.50,
    "total_gallons_needed": 279.1,
    "num_stops": 5,
    "vehicle_range_miles": 500,
    "vehicle_mpg": 10
  },
  "map_url": "/api/map/?start=New+York,+NY&end=Los+Angeles,+CA"
}
```

### `GET /api/map/?start=...&end=...`

Opens an interactive dark-themed map in the browser showing:
- Blue route polyline
- Green start marker
- Red end marker
- Orange fuel stop markers (click for details)

---

## Architecture

```
POST /api/route/
  ↓
1. Geocode start + end  (Nominatim, cached)
  ↓
2. Fetch route polyline  (OSRM — 1 API call)
  ↓
3. Find stations within 15 miles of route
   (bounding-box pre-filter + exact haversine projection)
  ↓
4. DP optimizer: cheapest stops where gap ≤ 500 miles
   Cost = (miles / 10 mpg) × price_per_gallon
  ↓
5. Return JSON + Leaflet map URL
```

### Algorithm (optimizer.py)
- Nodes: `[START] + [stations sorted by route_dist] + [END]`
- Edge: `i → j` if `distance ≤ 500 miles`
- Cost: `(distance / 10) × price_at_i`  (buy exactly enough fuel to reach next stop)
- DP: `dp[j] = min(dp[i] + cost(i→j))` for all reachable j from i
- Complexity: O(N²) where N = stations on route (~50–200 typically)

---

## Testing with Postman

1. Import and send: `POST http://localhost:8000/api/route/`
2. Body (JSON): `{"start": "Chicago, IL", "end": "Dallas, TX"}`
3. Open `map_url` in browser to see the visual map

---

## Tech Stack

- **Django 4.2 LTS** + **Django REST Framework** (stable and Python 3.9 compatible)
- **django-cors-headers** (CORS preflight support)
- **geopy** (Nominatim + Photon geocoding)
- **requests** (OSRM API)
- **SQLite** (station database)
- **Leaflet.js** (map rendering)

---

## Specmatic Contract Testing (Offline Service Virtualization)

We use **Specmatic** to validate our REST API against an OpenAPI contract and to virtualize (stub) all three external dependencies (OSRM, Nominatim, Photon). This allows our tests to run completely offline, reliably, and instantly on every run.

### Prerequisites
- Java 21 (required by Specmatic engine)
- Node.js / `npx`

### How to Run the Contract Tests

1. **Start the Specmatic Stub Server:**
   This reads the local API specs and hosts the OSRM, Nominatim, and Photon mock services on port 9000:
   ```bash
   npx specmatic stub --config=specmatic.json
   ```

2. **Start the Django Server in Testing Mode:**
   Start the Django server on port 8005 and point the external service environment variables to the local stub server:
   ```bash
   OSRM_BASE_URL=http://localhost:9000/route/v1/driving \
   NOMINATIM_URL=http://localhost:9000 \
   PHOTON_URL=http://localhost:9000 \
   python manage.py runserver 8005
   ```

3. **Run the Contract Tests:**
   This runs the contract tests against the Django API using the configured test examples:
   ```bash
   npx specmatic test --host localhost --port 8005 --config=specmatic.json
   ```

