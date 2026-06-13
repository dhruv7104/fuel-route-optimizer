from django.db import models


class FuelStation(models.Model):
    """
    Represents a fuel station from the OPIS dataset.
    Geocoded lat/lng is populated by the load_stations management command.
    """
    opis_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField()
    retail_price = models.FloatField()  # USD per gallon
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "retail_price"]),
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return f"{self.name} – {self.city}, {self.state} (${self.retail_price:.3f}/gal)"

    def to_dict(self):
        return {
            "opis_id": self.opis_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "retail_price": round(self.retail_price, 5),
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
