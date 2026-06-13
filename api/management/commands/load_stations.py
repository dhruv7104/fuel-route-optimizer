"""
Management command: load_stations

Reads fuel-prices-for-be-assessment.csv, deduplicates stations
(keeping the cheapest price per OPIS ID + city + state), geocodes each
unique (city, state) via Nominatim, and upserts into the FuelStation table.

Usage:
    python manage.py load_stations

Options:
    --batch N     Geocode at most N unique city/state combos (default: all)
    --clear       Delete all existing stations before loading
    --no-geocode  Skip geocoding (load CSV data only, lat/lng will be NULL)
"""
import csv
import time
import logging
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from api.models import FuelStation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Load fuel station data from CSV and geocode each unique city/state."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=None,
                            help="Limit geocoding to N city/state pairs (for testing).")
        parser.add_argument("--clear", action="store_true",
                            help="Clear existing stations before loading.")
        parser.add_argument("--no-geocode", action="store_true",
                            help="Skip geocoding; lat/lng will remain NULL.")

    def handle(self, *args, **options):
        csv_path: Path = settings.FUEL_CSV_PATH

        if not csv_path.exists():
            self.stderr.write(self.style.ERROR(f"CSV not found: {csv_path}"))
            return

        if options["clear"]:
            count, _ = FuelStation.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {count} existing stations."))

        # ── Parse CSV ──────────────────────────────────────────────────────────
        self.stdout.write("Reading CSV…")
        raw: dict[tuple, dict] = {}  # (opis_id, city, state) → cheapest row

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    price   = float(row["Retail Price"])
                    city    = row["City"].strip()
                    state   = row["State"].strip()

                    # Skip non-US entries (e.g. Canadian stations)
                    if len(state) != 2 or not state.isalpha():
                        continue

                    key = (opis_id, city.lower(), state.upper())
                    if key not in raw or price < raw[key]["retail_price"]:
                        raw[key] = {
                            "opis_id":      opis_id,
                            "name":         row["Truckstop Name"].strip(),
                            "address":      row["Address"].strip(),
                            "city":         city,
                            "state":        state.upper(),
                            "rack_id":      int(row["Rack ID"]) if row["Rack ID"].strip() else 0,
                            "retail_price": price,
                        }
                except (ValueError, KeyError):
                    continue

        self.stdout.write(f"Unique stations: {len(raw)}")

        # ── Geocode unique (city, state) pairs ────────────────────────────────
        coord_cache: dict[tuple, tuple | None] = {}
        
        # Pre-populate cache with already geocoded stations in the DB
        existing_geocoded = FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
        for s in existing_geocoded:
            coord_cache[(s.city.lower(), s.state.upper())] = (s.latitude, s.longitude)
        
        if coord_cache:
            self.stdout.write(f"Loaded {len(coord_cache)} unique city/state coordinates from DB cache.")

        if not options["no_geocode"]:
            import sys
            from geopy.geocoders import Nominatim, Photon
            from geopy.exc import GeocoderTimedOut, GeocoderServiceError

            geolocator = Nominatim(user_agent="fuel_route_optimizer_loader_v1", timeout=10)
            photon_geolocator = Photon(timeout=10)

            unique_locations = {(r["city"].strip(), r["state"].strip()) for r in raw.values()}
            
            # Filter out locations that are already geocoded in the cache
            to_geocode = [
                (city, state) for city, state in unique_locations
                if (city.lower(), state.upper()) not in coord_cache
            ]

            if options["batch"]:
                to_geocode = to_geocode[: options["batch"]]

            total = len(to_geocode)
            if total > 0:
                self.stdout.write(f"Geocoding {total} new unique city/state pairs (this may take a while)…")
                sys.stdout.flush()

                for i, (city, state) in enumerate(to_geocode, 1):
                    key = (city.lower(), state.upper())
                    query = f"{city}, {state}, USA"
                    try:
                        loc = geolocator.geocode(query, exactly_one=True)
                        if not loc:
                            loc = photon_geolocator.geocode(query, exactly_one=True)
                        if loc:
                            lat, lng = loc.latitude, loc.longitude
                            coord_cache[key] = (lat, lng)
                            # Immediately update matching stations in DB
                            updated_count = FuelStation.objects.filter(
                                city__iexact=city, state__iexact=state
                            ).update(latitude=lat, longitude=lng)
                            self.stdout.write(f"  [{i}/{total}] Geocoded: {query} -> ({lat}, {lng}). Updated {updated_count} stations in DB.")
                        else:
                            coord_cache[key] = None
                            self.stdout.write(f"  [{i}/{total}] Geocoded (No Results): {query}")
                    except Exception as exc:
                        # Fallback to Photon in case of rate limits/timeouts
                        try:
                            loc = photon_geolocator.geocode(query, exactly_one=True)
                            if loc:
                                lat, lng = loc.latitude, loc.longitude
                                coord_cache[key] = (lat, lng)
                                updated_count = FuelStation.objects.filter(
                                    city__iexact=city, state__iexact=state
                                ).update(latitude=lat, longitude=lng)
                                self.stdout.write(f"  [{i}/{total}] Geocoded via Photon Fallback: {query} -> ({lat}, {lng}). Updated {updated_count} stations in DB.")
                            else:
                                coord_cache[key] = None
                                self.stdout.write(f"  [{i}/{total}] Geocoded (No Results after fallback): {query}")
                        except Exception as fallback_exc:
                            self.stderr.write(f"  [{i}/{total}] Geocoding failed: {query}: {exc} (Fallback failed: {fallback_exc})")
                            sys.stderr.flush()
                            coord_cache[key] = None

                    sys.stdout.flush()
                    time.sleep(1.1)  # Nominatim: 1 req/sec
            else:
                self.stdout.write("All locations are already geocoded. Skipping Nominatim calls.")
                sys.stdout.flush()

        # ── Upsert stations into DB ───────────────────────────────────────────
        self.stdout.write("Saving to database…")
        import sys
        sys.stdout.flush()
        created = updated = skipped = 0

        for data in raw.values():
            key = (data["city"].lower(), data["state"].upper())
            coords = coord_cache.get(key)
            
            lat = None
            lng = None
            if coords:
                lat, lng = coords
            else:
                # Try to look up existing station to preserve coords
                existing = FuelStation.objects.filter(opis_id=data["opis_id"]).first()
                if existing:
                    lat = existing.latitude
                    lng = existing.longitude

            obj, is_new = FuelStation.objects.update_or_create(
                opis_id=data["opis_id"],
                city=data["city"],
                state=data["state"],
                defaults={
                    "name":         data["name"],
                    "address":      data["address"],
                    "rack_id":      data["rack_id"],
                    "retail_price": data["retail_price"],
                    "latitude":     lat,
                    "longitude":    lng,
                },
            )
            if is_new:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {created}, Updated: {updated}, Skipped: {skipped}"
            )
        )
        geocoded_count = FuelStation.objects.filter(latitude__isnull=False).count()
        self.stdout.write(
            self.style.SUCCESS(f"Geocoded stations in DB: {geocoded_count}")
        )

        # Reload in-memory store
        from api.services import station_store
        station_store.reload()
        self.stdout.write(self.style.SUCCESS("In-memory station store reloaded."))

