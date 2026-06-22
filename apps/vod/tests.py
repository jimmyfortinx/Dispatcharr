from unittest.mock import Mock, call, patch

import requests
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory
from rest_framework.test import force_authenticate

from apps.m3u.models import M3UAccount
from apps.vod.api_views import (
    MovieViewSet,
    UnifiedContentViewSet,
    VODCategoryViewSet,
    VODLogoViewSet,
)
from apps.vod.models import (
    M3UMovieRelation,
    M3USeriesRelation,
    M3UVODCategoryRelation,
    Movie,
    Series,
    VODCategory,
    VODLogo,
)


User = get_user_model()


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


class MovieViewSetVisibilityTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            username="vod-user",
            password="testpass123",
            user_level=10,
        )
        self.view = MovieViewSet.as_view({"get": "list"})

    def test_list_excludes_relations_from_accounts_with_vod_disabled(self):
        enabled_account = M3UAccount.objects.create(
            name="Enabled VOD Account",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": True, "mac": "00:1A:79:00:00:91"},
        )
        disabled_account = M3UAccount.objects.create(
            name="Disabled VOD Account",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": False, "mac": "00:1A:79:00:00:92"},
        )
        category = VODCategory.objects.create(name="Movies", category_type="movie")
        enabled_movie = Movie.objects.create(name="Visible Movie")
        hidden_movie = Movie.objects.create(name="Hidden Movie")

        M3UVODCategoryRelation.objects.create(
            m3u_account=enabled_account,
            category=category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=disabled_account,
            category=category,
            enabled=True,
        )
        M3UMovieRelation.objects.create(
            m3u_account=enabled_account,
            movie=enabled_movie,
            category=category,
            stream_id="visible-movie",
        )
        M3UMovieRelation.objects.create(
            m3u_account=disabled_account,
            movie=hidden_movie,
            category=category,
            stream_id="hidden-movie",
        )

        request = self.factory.get("/api/vod/movies/")
        force_authenticate(request, user=self.user)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.data["results"]}
        self.assertIn("Visible Movie", names)
        self.assertNotIn("Hidden Movie", names)


class UnifiedContentVisibilityTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            username="unified-vod-user",
            password="testpass123",
            user_level=10,
        )
        self.view = UnifiedContentViewSet.as_view({"get": "list"})

    def test_all_view_excludes_accounts_with_vod_disabled(self):
        enabled_account = M3UAccount.objects.create(
            name="Unified Enabled VOD",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": True, "mac": "00:1A:79:00:00:93"},
        )
        disabled_account = M3UAccount.objects.create(
            name="Unified Disabled VOD",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": False, "mac": "00:1A:79:00:00:94"},
        )
        category = VODCategory.objects.create(name="Series", category_type="series")
        visible_series = Series.objects.create(name="Visible Series")
        hidden_series = Series.objects.create(name="Hidden Series")

        M3UVODCategoryRelation.objects.create(
            m3u_account=enabled_account,
            category=category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=disabled_account,
            category=category,
            enabled=True,
        )
        M3USeriesRelation.objects.create(
            m3u_account=enabled_account,
            series=visible_series,
            category=category,
            external_series_id="visible-series",
        )
        M3USeriesRelation.objects.create(
            m3u_account=disabled_account,
            series=hidden_series,
            category=category,
            external_series_id="hidden-series",
        )

        request = self.factory.get("/api/vod/all/?page=1&page_size=24")
        force_authenticate(request, user=self.user)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.data["results"]}
        self.assertIn("Visible Series", names)
        self.assertNotIn("Hidden Series", names)


class VODCategoryVisibilityTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            username="vod-category-user",
            password="testpass123",
            user_level=10,
        )
        self.view = VODCategoryViewSet.as_view({"get": "list"})

    def test_categories_only_include_visible_content(self):
        enabled_account = M3UAccount.objects.create(
            name="Category Enabled VOD",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": True, "mac": "00:1A:79:00:00:95"},
        )
        disabled_account = M3UAccount.objects.create(
            name="Category Disabled VOD",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": False, "mac": "00:1A:79:00:00:96"},
        )
        visible_category = VODCategory.objects.create(
            name="Visible Category",
            category_type="movie",
        )
        hidden_category = VODCategory.objects.create(
            name="Hidden Category",
            category_type="movie",
        )
        visible_movie = Movie.objects.create(name="Visible Category Movie")
        hidden_movie = Movie.objects.create(name="Hidden Category Movie")

        M3UVODCategoryRelation.objects.create(
            m3u_account=enabled_account,
            category=visible_category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=disabled_account,
            category=hidden_category,
            enabled=True,
        )
        M3UMovieRelation.objects.create(
            m3u_account=enabled_account,
            movie=visible_movie,
            category=visible_category,
            stream_id="visible-category-movie",
        )
        M3UMovieRelation.objects.create(
            m3u_account=disabled_account,
            movie=hidden_movie,
            category=hidden_category,
            stream_id="hidden-category-movie",
        )

        request = self.factory.get("/api/vod/categories/")
        force_authenticate(request, user=self.user)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        category_names = {row["name"] for row in response.data}
        self.assertIn("Visible Category", category_names)
        self.assertNotIn("Hidden Category", category_names)
