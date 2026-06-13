from django.urls import path
from .views import RouteView, MapView

urlpatterns = [
    path("route/", RouteView.as_view(), name="route"),
    path("map/", MapView.as_view(), name="map"),
]
