from django.urls import path, re_path
from .views import m3u_endpoint, epg_endpoint

app_name = "output"

urlpatterns = [
    path("m3u", m3u_endpoint, name="generate_m3u"),
    path("epg", epg_endpoint, name="generate_epg"),
    # Allow `/m3u`, `/m3u/`, `/m3u/profile_name`, and `/m3u/profile_name/`
    re_path(r"^m3u(?:/(?P<profile_name>[^/]+))?/?$", m3u_endpoint, name="m3u_endpoint"),
    # Allow `/epg`, `/epg/`, `/epg/profile_name`, and `/epg/profile_name/`
    re_path(r"^epg(?:/(?P<profile_name>[^/]+))?/?$", epg_endpoint, name="epg_endpoint"),
]
