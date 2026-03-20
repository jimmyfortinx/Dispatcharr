from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerVodCategoryDiscoveryResult
from apps.vod.models import M3UVODCategoryRelation, VODCategory
from apps.vod.tasks import refresh_categories, refresh_vod_content


User = get_user_model()


class StalkerPhase11CategoryDiscoveryTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker VOD Categories",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={
                "mac": "00:1A:79:00:00:71",
                "enable_vod": True,
                "auto_enable_new_groups_vod": False,
                "auto_enable_new_groups_series": True,
                "token": "OLD-TOKEN",
            },
        )

    @patch("apps.vod.tasks.StalkerClient.discover_vod_categories")
    def test_refresh_categories_maps_stalker_metadata_into_relations(
        self,
        mock_discover_vod_categories,
    ):
        mock_discover_vod_categories.return_value = StalkerVodCategoryDiscoveryResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            movie_categories=[
                {"id": "10", "title": "Action"},
                {"category_id": "11", "name": "Drama"},
            ],
            series_categories=[
                {"id": "20", "title": "Shows"},
            ],
            token="TOKEN-NEW",
            used_authentication=True,
        )

        movie_map, series_map = refresh_categories(self.account.id)

        self.assertEqual(movie_map["10"].name, "Action")
        self.assertEqual(movie_map["11"].name, "Drama")
        self.assertEqual(series_map["20"].name, "Shows")

        action_relation = M3UVODCategoryRelation.objects.get(
            m3u_account=self.account,
            category__name="Action",
            category__category_type="movie",
        )
        shows_relation = M3UVODCategoryRelation.objects.get(
            m3u_account=self.account,
            category__name="Shows",
            category__category_type="series",
        )

        self.assertFalse(action_relation.enabled)
        self.assertEqual(
            action_relation.custom_properties,
            {
                "stalker_category_id": "10",
                "stalker_category_type": "movie",
            },
        )
        self.assertEqual(
            shows_relation.custom_properties,
            {
                "stalker_category_id": "20",
                "stalker_category_type": "series",
            },
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.custom_properties["token"], "TOKEN-NEW")

    def test_client_discovers_categories_from_distinct_movie_and_series_surfaces(self):
        from apps.m3u.stalker import StalkerClient

        client = StalkerClient(
            server_url="http://portal.example.com/c/",
            mac="00:1A:79:00:00:74",
        )

        with (
            patch.object(client, "handshake"),
            patch.object(client, "get_profile", return_value={"name": "Demo"}),
            patch.object(
                client,
                "get_vod_categories",
                return_value=[
                    {"id": "10", "title": "Movies"},
                ],
            ),
            patch.object(
                client,
                "get_series_categories",
                return_value=[
                    {"id": "20", "title": "FRENCH SERIE"},
                ],
            ),
        ):
            result = client.discover_vod_categories()

        self.assertEqual(
            [category["title"] for category in result.movie_categories],
            ["Movies"],
        )
        self.assertEqual(
            [category["title"] for category in result.series_categories],
            ["FRENCH SERIE"],
        )

    def test_get_vod_series_uses_series_ordered_list_endpoint(self):
        from apps.m3u.stalker import StalkerClient

        client = StalkerClient(
            server_url="http://portal.example.com/c/",
            mac="00:1A:79:00:00:75",
        )

        with patch.object(
            client,
            "_request",
            return_value={"js": {"data": [{"id": "7359:7359", "name": "Fatal Seduction"}]}},
        ) as mock_request:
            series = client.get_vod_series(
                "http://portal.example.com/stalker_portal/server/load.php",
                category_id="4",
            )

        self.assertEqual(series[0]["id"], "7359:7359")
        mock_request.assert_called_once_with(
            "GET",
            "http://portal.example.com/stalker_portal/server/load.php",
            query={
                "type": "series",
                "action": "get_ordered_list",
                "JsHttpRequest": "1-xml",
                "p": 1,
                "category": "4",
            },
            with_auth=True,
        )

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.vod.tasks.refresh_series")
    @patch("apps.vod.tasks.refresh_movies")
    @patch("apps.vod.tasks.refresh_categories")
    def test_refresh_vod_content_for_stalker_stops_after_category_sync(
        self,
        mock_refresh_categories,
        mock_refresh_movies,
        mock_refresh_series,
        mock_send_m3u_update,
    ):
        mock_refresh_categories.return_value = (
            {"10": object(), "11": object()},
            {"20": object()},
        )

        result = refresh_vod_content(self.account.id)

        self.assertIn("Stalker VOD category refresh completed", result)
        mock_refresh_categories.assert_called_once_with(self.account.id)
        mock_refresh_movies.assert_not_called()
        mock_refresh_series.assert_not_called()

        success_update = mock_send_m3u_update.call_args_list[-1]
        self.assertEqual(success_update.args[0], self.account.id)
        self.assertEqual(success_update.args[1], "vod_refresh")
        self.assertEqual(success_update.args[2], 100)
        self.assertEqual(success_update.kwargs["status"], "success")
        self.assertIn("2 movie categories, 1 series categories", success_update.kwargs["message"])


class StalkerPhase11CategorySettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.account = M3UAccount.objects.create(
            name="Stalker Category Settings",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:72",
                "enable_vod": True,
            },
        )
        self.category = VODCategory.objects.create(
            name="Action",
            category_type="movie",
        )
        self.relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "10",
                "stalker_category_type": "movie",
                "raw_name": "Action",
            },
        )

    def test_group_settings_update_preserves_stalker_category_metadata(self):
        response = self.client.patch(
            f"/api/m3u/accounts/{self.account.id}/group-settings/",
            {
                "group_settings": [],
                "category_settings": [
                    {
                        "id": self.category.id,
                        "enabled": False,
                        "custom_properties": {
                            "raw_name": "Action Updated",
                        },
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.relation.refresh_from_db()
        self.assertFalse(self.relation.enabled)
        self.assertEqual(self.relation.custom_properties["stalker_category_id"], "10")
        self.assertEqual(self.relation.custom_properties["stalker_category_type"], "movie")
        self.assertEqual(self.relation.custom_properties["raw_name"], "Action Updated")


class StalkerPhase11RefreshEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.account = M3UAccount.objects.create(
            name="Stalker Refresh",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:73",
                "enable_vod": True,
            },
        )

    @patch("apps.vod.tasks.refresh_vod_content.delay")
    def test_refresh_vod_endpoint_accepts_stalker_accounts(self, mock_delay):
        response = self.client.post(f"/api/m3u/accounts/{self.account.id}/refresh-vod/")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once_with(self.account.id)
