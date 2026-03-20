from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.m3u.serializers import M3UAccountSerializer
from apps.m3u.stalker import StalkerClient, StalkerVodDiscoveryResult
from apps.vod.models import M3UVODCategoryRelation


User = get_user_model()


class StalkerPhase10SerializerTests(TestCase):
    def test_stalker_create_persists_vod_flag(self):
        serializer = M3UAccountSerializer(
            data={
                "name": "Stalker VOD",
                "account_type": M3UAccount.Types.STALKER,
                "server_url": "http://portal.example.com/c/",
                "mac": "00:1A:79:00:00:60",
                "enable_vod": True,
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        account = serializer.save()

        self.assertTrue(account.custom_properties["enable_vod"])
        self.assertTrue(M3UAccountSerializer(account).data["enable_vod"])


class StalkerPhase10CategoryVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_vod_category_list_creates_uncategorized_relations_for_stalker_accounts(self):
        account = M3UAccount.objects.create(
            name="Stalker Categories",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:61",
                "enable_vod": True,
                "auto_enable_new_groups_vod": False,
                "auto_enable_new_groups_series": True,
            },
        )

        response = self.client.get("/api/vod/categories/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        movie_relation = M3UVODCategoryRelation.objects.get(
            m3u_account=account,
            category__name="Uncategorized",
            category__category_type="movie",
        )
        series_relation = M3UVODCategoryRelation.objects.get(
            m3u_account=account,
            category__name="Uncategorized",
            category__category_type="series",
        )

        self.assertFalse(movie_relation.enabled)
        self.assertTrue(series_relation.enabled)


class StalkerPhase10DiscoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.account = M3UAccount.objects.create(
            name="Stalker Discovery",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={"mac": "00:1A:79:00:00:62"},
        )

    @patch("apps.m3u.api_views.StalkerClient.discover_vod_protocol")
    def test_discover_vod_protocol_persists_samples_on_account(self, mock_discover):
        mock_discover.return_value = StalkerVodDiscoveryResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            samples={
                "movie_categories": [{"id": "1", "title": "Movies"}],
                "series_categories": [{"id": "2", "title": "Series"}],
                "movie_list": [{"id": "100", "name": "Movie"}],
                "series_list": [{"id": "200", "name": "Series"}],
                "series_detail": [{"id": "300", "name": "Season 1"}],
                "episodes": [{"id": "400", "name": "Episode 1"}],
                "vod_link": "http://media.example.com/movie.mp4",
            },
            token="TOKEN-NEW",
            used_authentication=True,
        )

        response = self.client.post(
            f"/api/m3u/accounts/{self.account.id}/discover-vod-protocol/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.account.refresh_from_db()
        self.assertEqual(self.account.custom_properties["token"], "TOKEN-NEW")
        self.assertIn(
            "stalker_vod_protocol_samples",
            self.account.custom_properties,
        )
        self.assertEqual(
            self.account.custom_properties["stalker_vod_protocol_samples"]["vod_link"],
            "http://media.example.com/movie.mp4",
        )


class StalkerPhase10ClientTests(TestCase):
    def setUp(self):
        self.client = StalkerClient(
            server_url="http://portal.example.com/c/",
            mac="00:1A:79:00:00:63",
        )

    def test_get_vod_categories_uses_vod_endpoint(self):
        with patch.object(
            self.client,
            "_request",
            return_value={"js": [{"id": "1", "title": "Movies"}]},
        ) as mock_request:
            categories = self.client.get_vod_categories(
                "http://portal.example.com/stalker_portal/server/load.php"
            )

        self.assertEqual(categories[0]["id"], "1")
        mock_request.assert_called_once_with(
            "GET",
            "http://portal.example.com/stalker_portal/server/load.php",
            query={
                "type": "vod",
                "action": "get_categories",
                "JsHttpRequest": "1-xml",
            },
            with_auth=True,
        )

    def test_get_series_seasons_uses_movie_season_episode_query_shape(self):
        with patch.object(
            self.client,
            "_request",
            return_value={"js": {"data": [{"id": "11", "name": "Season 1"}]}},
        ) as mock_request:
            seasons = self.client.get_series_seasons(
                "http://portal.example.com/stalker_portal/server/load.php",
                series_id="99",
            )

        self.assertEqual(seasons[0]["id"], "11")
        mock_request.assert_called_once_with(
            "GET",
            "http://portal.example.com/stalker_portal/server/load.php",
            query={
                "type": "vod",
                "action": "get_ordered_list",
                "JsHttpRequest": "1-xml",
                "p": 1,
                "movie_id": "99",
                "season_id": "0",
                "episode_id": "0",
            },
            with_auth=True,
        )

    def test_create_vod_link_uses_vod_type(self):
        with patch.object(
            self.client,
            "_request",
            return_value={"js": {"cmd": "ffmpeg http://media.example.com/movie.mp4"}},
        ) as mock_request:
            resolved = self.client.create_vod_link(
                "http://portal.example.com/stalker_portal/server/load.php",
                "ffmpeg http://provider.example.com/play/movie",
            )

        self.assertEqual(resolved, "http://media.example.com/movie.mp4")
        request_url = mock_request.call_args.args[1]
        self.assertIn("action=create_link&type=vod", request_url)
