from unittest.mock import Mock, call, patch

import requests
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.vod.api_views import VODLogoViewSet
from apps.vod.models import VODLogo


class VODLogoCacheViewSetTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = VODLogoViewSet.as_view({"get": "cache"})

    @patch("apps.vod.api_views.requests.get")
    def test_cache_retries_http_after_https_ssl_error(self, mock_get):
        logo = VODLogo.objects.create(
            name="Diablo poster",
            url="https://diablo-pro.com:2095/images/poster.jpg",
        )
        http_response = Mock()
        http_response.raise_for_status.return_value = None
        http_response.headers = {"Content-Type": "image/jpeg"}
        http_response.iter_content.return_value = [b"poster-bytes"]
        mock_get.side_effect = [
            requests.exceptions.SSLError("wrong version number"),
            http_response,
        ]

        response = self.view(
            self.factory.get(f"/api/vod/vodlogos/{logo.id}/cache/"),
            pk=str(logo.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(list(response.streaming_content), [b"poster-bytes"])
        self.assertEqual(
            mock_get.call_args_list,
            [
                call(logo.url, stream=True, timeout=10),
                call(
                    "http://diablo-pro.com:2095/images/poster.jpg",
                    stream=True,
                    timeout=10,
                ),
            ],
        )

    @patch("apps.vod.api_views.requests.get")
    def test_cache_returns_404_when_http_fallback_fails(self, mock_get):
        logo = VODLogo.objects.create(
            name="Broken poster",
            url="https://diablo-pro.com:2095/images/missing.jpg",
        )
        mock_get.side_effect = [
            requests.exceptions.SSLError("wrong version number"),
            requests.exceptions.RequestException("connection failed"),
        ]

        response = self.view(
            self.factory.get(f"/api/vod/vodlogos/{logo.id}/cache/"),
            pk=str(logo.id),
        )

        self.assertEqual(response.status_code, 404)
