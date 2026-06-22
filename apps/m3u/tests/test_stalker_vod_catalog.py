from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.m3u.serializers import M3UAccountSerializer
from apps.m3u.stalker import StalkerClient, StalkerVodDiscoveryResult
from apps.vod.models import (
    M3UMovieRelation,
    M3USeriesRelation,
    M3UVODCategoryRelation,
    Movie,
    Series,
)


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


class StalkerPhase10DisableVodCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin-disable-vod",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.account = M3UAccount.objects.create(
            name="Stalker Disable VOD",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:64",
                "enable_vod": True,
            },
        )
        self.movie = Movie.objects.create(name="Movie To Remove")
        self.series = Series.objects.create(name="Series To Remove")
        M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.movie,
            stream_id="movie-1",
        )
        M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.series,
            external_series_id="series-1",
        )

    def test_disabling_vod_removes_existing_catalog_relations(self):
        response = self.client.patch(
            f"/api/m3u/accounts/{self.account.id}/",
            {"enable_vod": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            M3UMovieRelation.objects.filter(m3u_account=self.account).exists()
        )
        self.assertFalse(
            M3USeriesRelation.objects.filter(m3u_account=self.account).exists()
        )
        self.assertFalse(Movie.objects.filter(id=self.movie.id).exists())
        self.assertFalse(Series.objects.filter(id=self.series.id).exists())


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

    def test_get_series_categories_uses_series_endpoint(self):
        with patch.object(
            self.client,
            "_request",
            return_value={"js": [{"id": "4", "title": "FRENCH SERIE"}]},
        ) as mock_request:
            categories = self.client.get_series_categories(
                "http://portal.example.com/stalker_portal/server/load.php"
            )

        self.assertEqual(categories[0]["id"], "4")
        mock_request.assert_called_once_with(
            "GET",
            "http://portal.example.com/stalker_portal/server/load.php",
            query={
                "type": "series",
                "action": "get_categories",
                "JsHttpRequest": "1-xml",
            },
            with_auth=True,
        )

    def test_get_series_seasons_uses_series_season_episode_query_shape(self):
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
                "type": "series",
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


from unittest.mock import ANY, patch

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
    @patch("apps.vod.tasks.cleanup_orphaned_vod_content")
    @patch("apps.vod.tasks.refresh_series")
    @patch("apps.vod.tasks.refresh_movies")
    @patch("apps.vod.tasks.refresh_categories")
    def test_refresh_vod_content_for_stalker_runs_catalog_import_after_category_sync(
        self,
        mock_refresh_categories,
        mock_refresh_movies,
        mock_refresh_series,
        mock_cleanup_orphaned_vod_content,
        mock_send_m3u_update,
    ):
        mock_refresh_categories.return_value = (
            {"10": object(), "11": object()},
            {"20": object()},
        )
        mock_cleanup_orphaned_vod_content.return_value = "cleanup complete"

        result = refresh_vod_content(self.account.id)

        self.assertIn("Stalker VOD refresh completed", result)
        mock_refresh_categories.assert_called_once_with(self.account.id, client=ANY)
        mock_refresh_movies.assert_called_once()
        mock_refresh_series.assert_called_once()
        mock_cleanup_orphaned_vod_content.assert_called_once()

        success_update = mock_send_m3u_update.call_args_list[-1]
        self.assertEqual(success_update.args[0], self.account.id)
        self.assertEqual(success_update.args[1], "vod_refresh")
        self.assertEqual(success_update.args[2], 100)
        self.assertEqual(success_update.kwargs["status"], "success")
        self.assertIn("2 movie categories, 1 series categories", success_update.kwargs["message"])

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.vod.tasks.acquire_task_lock", return_value=False)
    def test_refresh_vod_content_skips_when_refresh_already_running(
        self,
        mock_acquire_task_lock,
        mock_send_m3u_update,
    ):
        result = refresh_vod_content(self.account.id)

        self.assertEqual(
            result,
            f"VOD refresh already running for account {self.account.id}",
        )
        mock_acquire_task_lock.assert_called_once_with(
            "refresh_vod_content",
            self.account.id,
        )
        mock_send_m3u_update.assert_called_once()
        self.assertEqual(mock_send_m3u_update.call_args.args[0], self.account.id)
        self.assertEqual(mock_send_m3u_update.call_args.args[1], "vod_refresh")
        self.assertEqual(mock_send_m3u_update.call_args.kwargs["status"], "warning")


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

    @patch("apps.m3u.api_views.is_task_lock_held", return_value=True)
    @patch("apps.vod.tasks.refresh_vod_content.delay")
    def test_refresh_vod_endpoint_rejects_duplicate_refreshes(
        self,
        mock_delay,
        mock_is_task_lock_held,
    ):
        response = self.client.post(f"/api/m3u/accounts/{self.account.id}/refresh-vod/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        mock_is_task_lock_held.assert_called_once_with(
            "refresh_vod_content",
            self.account.id,
        )
        mock_delay.assert_not_called()


import threading
import time
from datetime import timedelta
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone

from apps.m3u.models import M3UAccount
from apps.vod.models import (
    M3UMovieRelation,
    M3USeriesRelation,
    M3UVODCategoryRelation,
    Movie,
    Series,
    VODCategory,
)
from apps.vod.tasks import (
    cleanup_orphaned_vod_content,
    get_stalker_category_requests,
    iter_stalker_catalog_batches,
    process_movie_batch,
    process_series_batch,
    refresh_movies,
    refresh_series,
)


class StalkerPhase12MovieImportTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Movies",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:81",
                "enable_vod": True,
                "auto_enable_new_groups_vod": True,
            },
        )
        self.category = VODCategory.objects.create(
            name="Action",
            category_type="movie",
        )
        self.category_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "10",
                "stalker_category_type": "movie",
            },
        )
        self.scan_start_time = timezone.now()

    def test_refresh_movies_uses_requested_category_context_and_stable_ids(self):
        client = Mock()
        client.vod_portal_url = "http://portal.example.com/stalker_portal/server/load.php"
        client.get_vod_movies.side_effect = [
            [
                {
                    "id": "100",
                    "title": "Heat",
                    "plot": "Original description",
                    "year": "1995",
                    "rating": "7.8",
                    "genre": "Crime",
                    "screenshot_uri": "http://img.example.com/heat.jpg",
                    "cmd": "ffmpeg http://provider.example.com/movie-a",
                }
            ],
            [],
        ]

        refresh_movies(
            client,
            self.account,
            {"10": self.category},
            {self.category.id: self.category_relation},
            scan_start_time=self.scan_start_time,
        )

        relation = M3UMovieRelation.objects.get(m3u_account=self.account)
        movie = relation.movie

        self.assertEqual(movie.name, "Heat")
        self.assertEqual(movie.description, "Original description")
        self.assertEqual(movie.year, 1995)
        self.assertEqual(movie.rating, "7.8")
        self.assertEqual(movie.genre, "Crime")
        self.assertEqual(movie.logo.url, "http://img.example.com/heat.jpg")
        self.assertEqual(relation.stream_id, "100")
        self.assertEqual(relation.category, self.category)
        self.assertEqual(
            relation.custom_properties["basic_data"]["cmd"],
            "ffmpeg http://provider.example.com/movie-a",
        )

        self.assertEqual(client.get_vod_movies.call_args_list[0].args[0], client.vod_portal_url)
        self.assertEqual(
            client.get_vod_movies.call_args_list[0].kwargs,
            {"category_id": "10", "page": 1},
        )
        self.assertEqual(
            client.get_vod_movies.call_args_list[1].kwargs,
            {"category_id": "10", "page": 2},
        )

        client.reset_mock()
        client.get_vod_movies.side_effect = [
            [
                {
                    "id": "100",
                    "title": "Heat",
                    "plot": "Updated description",
                    "year": "1995",
                    "rating": "8.1",
                    "genre": "Crime",
                    "screenshot_uri": "http://img.example.com/heat-updated.jpg",
                    "cmd": "ffmpeg http://provider.example.com/movie-b",
                }
            ],
            [],
        ]

        refresh_movies(
            client,
            self.account,
            {"10": self.category},
            {self.category.id: self.category_relation},
            scan_start_time=self.scan_start_time + timedelta(minutes=5),
        )

        self.assertEqual(Movie.objects.count(), 1)
        self.assertEqual(M3UMovieRelation.objects.count(), 1)

        relation.refresh_from_db()
        movie.refresh_from_db()
        self.assertEqual(movie.description, "Updated description")
        self.assertEqual(movie.rating, "8.1")
        self.assertEqual(movie.logo.url, "http://img.example.com/heat-updated.jpg")
        self.assertEqual(relation.stream_id, "100")
        self.assertEqual(
            relation.custom_properties["basic_data"]["cmd"],
            "ffmpeg http://provider.example.com/movie-b",
        )

    def test_iter_stalker_catalog_batches_fetches_categories_in_parallel(self):
        class SharedCatalogState:
            def __init__(self):
                self.lock = threading.Lock()
                self.active_calls = 0
                self.max_active_calls = 0

        class CloneableCatalogClient:
            def __init__(self, responses, shared_state):
                self.responses = responses
                self.shared_state = shared_state
                self.vod_portal_url = (
                    "http://portal.example.com/stalker_portal/server/load.php"
                )

            def clone_for_parallel_catalog(self):
                return CloneableCatalogClient(self.responses, self.shared_state)

            def get_vod_movies(self, portal_url, category_id=None, page=1):
                assert (
                    portal_url
                    == "http://portal.example.com/stalker_portal/server/load.php"
                )
                with self.shared_state.lock:
                    self.shared_state.active_calls += 1
                    self.shared_state.max_active_calls = max(
                        self.shared_state.max_active_calls,
                        self.shared_state.active_calls,
                    )
                try:
                    time.sleep(0.05)
                    return self.responses.get((str(category_id), page), [])
                finally:
                    with self.shared_state.lock:
                        self.shared_state.active_calls -= 1

        extra_category = VODCategory.objects.create(
            name="Drama",
            category_type="movie",
        )
        extra_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=extra_category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "11",
                "stalker_category_type": "movie",
            },
        )
        self.account.custom_properties = {
            **(self.account.custom_properties or {}),
            "stalker_vod_catalog_workers": 2,
        }
        self.account.save(update_fields=["custom_properties"])

        shared_state = SharedCatalogState()
        client = CloneableCatalogClient(
            responses={
                ("10", 1): [{"id": "100", "title": "Alpha"}],
                ("10", 2): [],
                ("11", 1): [{"id": "200", "title": "Beta"}],
                ("11", 2): [],
            },
            shared_state=shared_state,
        )

        batches = list(
            iter_stalker_catalog_batches(
                client,
                self.account,
                {
                    "10": self.category,
                    "11": extra_category,
                },
                {
                    self.category.id: self.category_relation,
                    extra_category.id: extra_relation,
                },
                content_type="movie",
            )
        )

        self.assertGreaterEqual(shared_state.max_active_calls, 2)
        self.assertEqual(
            [batch[0]["_requested_category_id"] for batch in batches],
            ["10", "11"],
        )
        self.assertEqual(
            [batch[0]["title"] for batch in batches],
            ["Alpha", "Beta"],
        )

    def test_iter_stalker_catalog_batches_pipelines_pages_within_category(self):
        class SharedCatalogState:
            def __init__(self):
                self.lock = threading.Lock()
                self.active_calls = 0
                self.max_active_calls = 0

        class CloneableCatalogClient:
            def __init__(self, responses, shared_state):
                self.responses = responses
                self.shared_state = shared_state
                self.vod_portal_url = (
                    "http://portal.example.com/stalker_portal/server/load.php"
                )

            def clone_for_parallel_catalog(self):
                return CloneableCatalogClient(self.responses, self.shared_state)

            def get_vod_movies(self, portal_url, category_id=None, page=1):
                assert (
                    portal_url
                    == "http://portal.example.com/stalker_portal/server/load.php"
                )
                with self.shared_state.lock:
                    self.shared_state.active_calls += 1
                    self.shared_state.max_active_calls = max(
                        self.shared_state.max_active_calls,
                        self.shared_state.active_calls,
                    )
                try:
                    time.sleep(0.05)
                    return self.responses.get((str(category_id), page), [])
                finally:
                    with self.shared_state.lock:
                        self.shared_state.active_calls -= 1

        self.account.custom_properties = {
            **(self.account.custom_properties or {}),
            "stalker_vod_catalog_workers": 1,
            "stalker_vod_catalog_page_workers": 2,
        }
        self.account.save(update_fields=["custom_properties"])

        shared_state = SharedCatalogState()
        client = CloneableCatalogClient(
            responses={
                ("10", 1): [{"id": "100", "title": "Page 1"}],
                ("10", 2): [{"id": "101", "title": "Page 2"}],
                ("10", 3): [],
            },
            shared_state=shared_state,
        )

        batches = list(
            iter_stalker_catalog_batches(
                client,
                self.account,
                {"10": self.category},
                {self.category.id: self.category_relation},
                content_type="movie",
            )
        )

        self.assertGreaterEqual(shared_state.max_active_calls, 2)
        self.assertEqual(
            [batch[0]["title"] for batch in batches],
            ["Page 1", "Page 2"],
        )

    def test_stalker_category_requests_prefers_specific_categories_over_all_bucket(self):
        requests = get_stalker_category_requests(
            {
                "*": self.category,
                "10": self.category,
                "__uncategorized__": self.category,
            }
        )

        self.assertEqual(requests, ["10"])

    def test_stalker_category_requests_only_include_enabled_categories(self):
        disabled_category = VODCategory.objects.create(
            name="Drama",
            category_type="movie",
        )
        disabled_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=disabled_category,
            enabled=False,
            custom_properties={
                "stalker_category_id": "11",
                "stalker_category_type": "movie",
            },
        )

        requests = get_stalker_category_requests(
            {
                "10": self.category,
                "11": disabled_category,
            },
            relations={
                self.category.id: self.category_relation,
                disabled_category.id: disabled_relation,
            },
        )

        self.assertEqual(requests, ["10"])

    def test_refresh_movies_only_requests_enabled_categories(self):
        disabled_category = VODCategory.objects.create(
            name="Drama",
            category_type="movie",
        )
        disabled_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=disabled_category,
            enabled=False,
            custom_properties={
                "stalker_category_id": "11",
                "stalker_category_type": "movie",
            },
        )
        client = Mock()
        client.vod_portal_url = "http://portal.example.com/stalker_portal/server/load.php"
        client.get_vod_movies.side_effect = [
            [],
        ]

        refresh_movies(
            client,
            self.account,
            {
                "10": self.category,
                "11": disabled_category,
            },
            {
                self.category.id: self.category_relation,
                disabled_category.id: disabled_relation,
            },
            scan_start_time=self.scan_start_time,
        )

        self.assertEqual(client.get_vod_movies.call_count, 1)
        self.assertEqual(
            client.get_vod_movies.call_args_list[0].kwargs,
            {"category_id": "10", "page": 1},
        )

    def test_refresh_movies_skips_provider_requests_when_no_categories_enabled(self):
        self.category_relation.enabled = False
        self.category_relation.save(update_fields=["enabled"])
        client = Mock()
        client.vod_portal_url = "http://portal.example.com/stalker_portal/server/load.php"
        refresh_movies(
            client,
            self.account,
            {"10": self.category},
            {self.category.id: self.category_relation},
            scan_start_time=self.scan_start_time,
        )

        client.get_vod_movies.assert_not_called()

    def test_process_movie_batch_keeps_stalker_relations_for_same_movie_across_categories(self):
        second_category = VODCategory.objects.create(
            name="Drama",
            category_type="movie",
        )
        second_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=second_category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "11",
                "stalker_category_type": "movie",
            },
        )

        seen_movie_keys = set()
        categories = {
            "10": self.category,
            "11": second_category,
        }
        relations = {
            self.category.id: self.category_relation,
            second_category.id: second_relation,
        }

        first_batch = [
            {
                "id": "1001",
                "title": "Heat",
                "year": "1995",
                "tmdb_id": "949",
                "category_id": "10",
                "cmd": "ffmpeg http://provider.example.com/movie-a",
            }
        ]
        second_batch = [
            {
                "id": "1002",
                "title": "Heat",
                "year": "1995",
                "tmdb_id": "949",
                "category_id": "11",
                "cmd": "ffmpeg http://provider.example.com/movie-b",
            }
        ]

        process_movie_batch(
            self.account,
            first_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_movie_keys=seen_movie_keys,
        )
        process_movie_batch(
            self.account,
            second_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_movie_keys=seen_movie_keys,
        )

        self.assertEqual(Movie.objects.count(), 1)
        self.assertEqual(M3UMovieRelation.objects.filter(m3u_account=self.account).count(), 2)
        self.assertEqual(
            set(
                M3UMovieRelation.objects.filter(m3u_account=self.account)
                .values_list("category__name", flat=True)
            ),
            {"Action", "Drama"},
        )


class StalkerPhase12SeriesImportTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Series",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:82",
                "enable_vod": True,
                "auto_enable_new_groups_series": True,
            },
        )
        self.category = VODCategory.objects.create(
            name="Shows",
            category_type="series",
        )
        self.category_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "20",
                "stalker_category_type": "series",
            },
        )
        self.scan_start_time = timezone.now()

    def test_refresh_series_imports_top_level_rows_idempotently(self):
        client = Mock()
        client.vod_portal_url = "http://portal.example.com/stalker_portal/server/load.php"
        client.get_vod_series.side_effect = [
            [
                {
                    "id": "7359:7359",
                    "title": "Fatal Seduction",
                    "plot": "Season one summary",
                    "year": "2023",
                    "rating": "8.3",
                    "genre": "Drama",
                    "cover": "http://img.example.com/fatal.jpg",
                    "release_date": "2023-07-07",
                }
            ],
            [],
        ]

        refresh_series(
            client,
            self.account,
            {"20": self.category},
            {self.category.id: self.category_relation},
            scan_start_time=self.scan_start_time,
        )

        relation = M3USeriesRelation.objects.get(m3u_account=self.account)
        series = relation.series

        self.assertEqual(series.name, "Fatal Seduction")
        self.assertEqual(series.description, "Season one summary")
        self.assertEqual(series.year, 2023)
        self.assertEqual(series.rating, "8.3")
        self.assertEqual(series.genre, "Drama")
        self.assertEqual(series.logo.url, "http://img.example.com/fatal.jpg")
        self.assertEqual(series.custom_properties["release_date"], "2023-07-07")
        self.assertEqual(relation.external_series_id, "7359:7359")
        self.assertEqual(relation.category, self.category)

        client.reset_mock()
        client.get_vod_series.side_effect = [
            [
                {
                    "id": "7359:7359",
                    "title": "Fatal Seduction",
                    "plot": "Updated series summary",
                    "year": "2023",
                    "rating": "8.5",
                    "genre": "Drama",
                    "cover": "http://img.example.com/fatal-updated.jpg",
                    "release_date": "2023-07-07",
                }
            ],
            [],
        ]

        refresh_series(
            client,
            self.account,
            {"20": self.category},
            {self.category.id: self.category_relation},
            scan_start_time=self.scan_start_time + timedelta(minutes=5),
        )

        self.assertEqual(Series.objects.count(), 1)
        self.assertEqual(M3USeriesRelation.objects.count(), 1)

        relation.refresh_from_db()
        series.refresh_from_db()
        self.assertEqual(series.description, "Updated series summary")
        self.assertEqual(series.rating, "8.5")
        self.assertEqual(series.logo.url, "http://img.example.com/fatal-updated.jpg")
        self.assertEqual(relation.external_series_id, "7359:7359")

    def test_process_series_batch_keeps_stalker_relations_for_same_series_across_categories(self):
        second_category = VODCategory.objects.create(
            name="Drama",
            category_type="series",
        )
        second_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=second_category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "21",
                "stalker_category_type": "series",
            },
        )

        seen_series_keys = set()
        categories = {
            "20": self.category,
            "21": second_category,
        }
        relations = {
            self.category.id: self.category_relation,
            second_category.id: second_relation,
        }

        first_batch = [
            {
                "id": "2001",
                "title": "Fatal Seduction",
                "year": "2023",
                "tmdb_id": "12345",
                "category_id": "20",
            }
        ]
        second_batch = [
            {
                "id": "2002",
                "title": "Fatal Seduction",
                "year": "2023",
                "tmdb_id": "12345",
                "category_id": "21",
            }
        ]

        process_series_batch(
            self.account,
            first_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_series_keys=seen_series_keys,
        )
        process_series_batch(
            self.account,
            second_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_series_keys=seen_series_keys,
        )

        self.assertEqual(Series.objects.count(), 1)
        self.assertEqual(M3USeriesRelation.objects.filter(m3u_account=self.account).count(), 2)
        self.assertEqual(
            set(
                M3USeriesRelation.objects.filter(m3u_account=self.account)
                .values_list("category__name", flat=True)
            ),
            {"Drama", "Shows"},
        )


class StalkerPhase12CleanupTests(TestCase):
    def setUp(self):
        self.reference_time = timezone.now()
        self.stalker_account = M3UAccount.objects.create(
            name="Stalker Cleanup",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:83", "enable_vod": True},
        )
        self.xc_account = M3UAccount.objects.create(
            name="XC Cleanup",
            account_type=M3UAccount.Types.XC,
            server_url="http://xc.example.com",
            username="demo",
            password="secret",
        )

    def test_account_scoped_cleanup_preserves_shared_content_with_other_providers(self):
        shared_movie = Movie.objects.create(name="Shared Movie")
        orphan_movie = Movie.objects.create(name="Orphan Movie")
        shared_series = Series.objects.create(name="Shared Series")
        orphan_series = Series.objects.create(name="Orphan Series")

        stale_seen = self.reference_time - timedelta(days=1)
        active_seen = self.reference_time + timedelta(minutes=1)

        stalker_movie_relation = M3UMovieRelation.objects.create(
            m3u_account=self.stalker_account,
            movie=shared_movie,
            stream_id="stalker-movie-1",
            last_seen=stale_seen,
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.xc_account,
            movie=shared_movie,
            stream_id="xc-movie-1",
            last_seen=active_seen,
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.stalker_account,
            movie=orphan_movie,
            stream_id="stalker-movie-2",
            last_seen=stale_seen,
        )

        stalker_series_relation = M3USeriesRelation.objects.create(
            m3u_account=self.stalker_account,
            series=shared_series,
            external_series_id="stalker-series-1",
            last_seen=stale_seen,
        )
        M3USeriesRelation.objects.create(
            m3u_account=self.xc_account,
            series=shared_series,
            external_series_id="xc-series-1",
            last_seen=active_seen,
        )
        M3USeriesRelation.objects.create(
            m3u_account=self.stalker_account,
            series=orphan_series,
            external_series_id="stalker-series-2",
            last_seen=stale_seen,
        )

        result = cleanup_orphaned_vod_content(
            account_id=self.stalker_account.id,
            scan_start_time=self.reference_time,
        )

        self.assertIn("Cleaned up 2 stale movie relations, 2 stale series relations", result)

        self.assertFalse(
            M3UMovieRelation.objects.filter(id=stalker_movie_relation.id).exists()
        )
        self.assertFalse(
            M3USeriesRelation.objects.filter(id=stalker_series_relation.id).exists()
        )
        self.assertTrue(Movie.objects.filter(id=shared_movie.id).exists())
        self.assertTrue(Series.objects.filter(id=shared_series.id).exists())
        self.assertFalse(Movie.objects.filter(id=orphan_movie.id).exists())
        self.assertFalse(Series.objects.filter(id=orphan_series.id).exists())


class VODCrossChunkDedupTests(TestCase):
    def setUp(self):
        self.xc_account = M3UAccount.objects.create(
            name="XC VOD",
            account_type=M3UAccount.Types.XC,
            server_url="http://xc.example.com",
            username="demo",
            password="secret",
            custom_properties={"enable_vod": True},
        )
        self.movie_category = VODCategory.objects.create(
            name="Movies",
            category_type="movie",
        )
        self.series_category = VODCategory.objects.create(
            name="Series",
            category_type="series",
        )
        self.movie_category_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.xc_account,
            category=self.movie_category,
            enabled=True,
        )
        self.series_category_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.xc_account,
            category=self.series_category,
            enabled=True,
        )
        self.scan_start_time = timezone.now()

    def test_process_movie_batch_deduplicates_same_movie_across_batches(self):
        seen_movie_keys = set()
        categories = {"1": self.movie_category}
        relations = {self.movie_category.id: self.movie_category_relation}

        first_batch = [
            {
                "stream_id": "1001",
                "name": "Dust Bunny",
                "tmdb_id": "1043197",
                "year": "2025",
                "category_id": "1",
            }
        ]
        second_batch = [
            {
                "stream_id": "1002",
                "name": "Dust Bunny DE",
                "tmdb_id": "1043197",
                "year": "2025",
                "category_id": "1",
            }
        ]

        process_movie_batch(
            self.xc_account,
            first_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_movie_keys=seen_movie_keys,
        )
        process_movie_batch(
            self.xc_account,
            second_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_movie_keys=seen_movie_keys,
        )

        self.assertEqual(Movie.objects.count(), 1)
        self.assertEqual(M3UMovieRelation.objects.count(), 1)
        self.assertEqual(
            M3UMovieRelation.objects.get().stream_id,
            "1001",
        )

    def test_process_movie_batch_handles_name_year_movies_without_external_ids(self):
        categories = {"1": self.movie_category}
        relations = {self.movie_category.id: self.movie_category_relation}

        process_movie_batch(
            self.xc_account,
            [
                {
                    "stream_id": "2001",
                    "name": "No External IDs",
                    "year": "2024",
                    "category_id": "1",
                }
            ],
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_movie_keys=set(),
        )

        movie = Movie.objects.get()
        relation = M3UMovieRelation.objects.get()

        self.assertEqual(movie.name, "No External IDs")
        self.assertEqual(movie.year, 2024)
        self.assertEqual(relation.stream_id, "2001")
        self.assertEqual(relation.movie, movie)

    def test_process_series_batch_deduplicates_same_series_across_batches(self):
        seen_series_keys = set()
        categories = {"2": self.series_category}
        relations = {self.series_category.id: self.series_category_relation}

        first_batch = [
            {
                "series_id": "2001",
                "name": "Fatal Seduction",
                "tmdb_id": "12345",
                "year": "2023",
                "category_id": "2",
            }
        ]
        second_batch = [
            {
                "series_id": "2002",
                "name": "Fatal Seduction DE",
                "tmdb_id": "12345",
                "year": "2023",
                "category_id": "2",
            }
        ]

        process_series_batch(
            self.xc_account,
            first_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_series_keys=seen_series_keys,
        )
        process_series_batch(
            self.xc_account,
            second_batch,
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_series_keys=seen_series_keys,
        )

        self.assertEqual(Series.objects.count(), 1)
        self.assertEqual(M3USeriesRelation.objects.count(), 1)
        self.assertEqual(
            M3USeriesRelation.objects.get().external_series_id,
            "2001",
        )

    def test_process_series_batch_handles_name_year_series_without_external_ids(self):
        categories = {"2": self.series_category}
        relations = {self.series_category.id: self.series_category_relation}

        process_series_batch(
            self.xc_account,
            [
                {
                    "series_id": "3001",
                    "name": "No External Series IDs",
                    "year": "2024",
                    "category_id": "2",
                }
            ],
            categories,
            relations,
            scan_start_time=self.scan_start_time,
            seen_series_keys=set(),
        )

        series = Series.objects.get()
        relation = M3USeriesRelation.objects.get()

        self.assertEqual(series.name, "No External Series IDs")
        self.assertEqual(series.year, 2024)
        self.assertEqual(relation.external_series_id, "3001")
        self.assertEqual(relation.series, series)


from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.vod.models import (
    Episode,
    Movie,
    M3UEpisodeRelation,
    M3UMovieRelation,
    M3USeriesRelation,
    M3UVODCategoryRelation,
    Series,
    VODCategory,
)
from apps.vod.tasks import (
    batch_refresh_series_episodes,
    process_series_batch,
    refresh_series_episodes,
    refresh_series_relation_episodes_task,
)


User = get_user_model()


class StalkerPhase13Base(TestCase):
    portal_url = "http://portal.example.com/stalker_portal/server/load.php"
    external_series_id = "7359:7359"

    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Series Detail",
            account_type=M3UAccount.Types.STALKER,
            server_url=self.portal_url,
            custom_properties={
                "mac": "00:1A:79:00:00:91",
                "enable_vod": True,
                "token": "TOKEN-EXISTING",
            },
        )
        self.series = Series.objects.create(name="Fatal Seduction")
        self.series_relation = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.series,
            external_series_id=self.external_series_id,
            custom_properties={
                "basic_data": {
                    "id": self.external_series_id,
                    "title": "Fatal Seduction",
                },
                "detailed_fetched": False,
                "episodes_fetched": False,
            },
        )

    def get_series_seasons_side_effect(self, portal_url, series_id, page=1):
        self.assertEqual(portal_url, self.portal_url)
        self.assertEqual(series_id, self.external_series_id)

        if page != 1:
            return []

        return [
            {
                "id": "5001",
                "title": "Season 1",
                "plot": "Detailed season summary",
                "rating": "8.7",
                "genre": "Drama",
                "year": "2023",
                "country": "South Africa",
            },
            {
                "id": "5002",
                "title": "Season 2",
            },
        ]

    def get_series_episodes_side_effect(self, portal_url, series_id, season_id, page=1):
        self.assertEqual(portal_url, self.portal_url)
        self.assertEqual(series_id, self.external_series_id)

        if page != 1:
            return []

        if season_id == "5001":
            return [
                {
                    "id": "9001",
                    "title": "Episode 1",
                    "series_number": "1",
                    "plot": "Pilot",
                    "rating": "8.1",
                    "release_date": "2023-07-07",
                    "movie_image": "http://img.example.com/ep1.jpg",
                    "cmd": "ffmpeg http://provider.example.com/ep1.mkv",
                }
            ]

        if season_id == "5002":
            return [
                {
                    "episode_id": "9002",
                    "title": "Episode 1",
                    "episode_number": "1",
                    "plot": "Season two premiere",
                    "rating": "8.4",
                    "cmd": "ffmpeg http://provider.example.com/ep2.mp4",
                }
            ]

        return []


class StalkerPhase13BatchSeriesRefreshTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Batch Episodes",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:95",
                "enable_vod": True,
            },
        )
        self.category = VODCategory.objects.create(
            name="Shows",
            category_type="series",
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "20",
                "stalker_category_type": "series",
            },
        )
        self.series_a = Series.objects.create(name="Series A")
        self.series_b = Series.objects.create(name="Series B")
        self.relation_a = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.series_a,
            category=self.category,
            external_series_id="1001",
        )
        self.relation_b = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.series_b,
            category=self.category,
            external_series_id="1002",
        )

    @patch("apps.vod.tasks.group")
    def test_batch_refresh_series_episodes_queues_stalker_relation_tasks(
        self,
        mock_group,
    ):
        captured_signatures = {}
        queued_job = Mock()
        queued_job.id = "group-123"
        group_result = Mock()
        group_result.apply_async.return_value = queued_job

        def build_group(signatures):
            captured_signatures["items"] = list(signatures)
            return group_result

        mock_group.side_effect = build_group
        from apps.vod import tasks as vod_tasks

        had_attr = hasattr(vod_tasks.current_app.conf, "task_always_eager")
        previous_value = getattr(vod_tasks.current_app.conf, "task_always_eager", None)
        vod_tasks.current_app.conf.task_always_eager = False
        try:
            result = batch_refresh_series_episodes(
                self.account.id,
                series_ids=[self.series_a.id, self.series_b.id],
            )
        finally:
            if had_attr:
                vod_tasks.current_app.conf.task_always_eager = previous_value
            else:
                delattr(vod_tasks.current_app.conf, "task_always_eager")

        self.assertEqual(mock_group.call_count, 1)
        self.assertEqual(group_result.apply_async.call_count, 1)
        self.assertIn("Queued batch episode refresh for 2 series", result)
        self.assertEqual(
            [sig.args for sig in captured_signatures["items"]],
            [(self.relation_a.id,), (self.relation_b.id,)],
        )
        self.assertTrue(
            all(
                sig.task == refresh_series_relation_episodes_task.name
                for sig in captured_signatures["items"]
            )
        )


class StalkerPhase13SeriesImportTests(StalkerPhase13Base):
    @patch("apps.vod.tasks.StalkerClient.get_series_episodes")
    @patch("apps.vod.tasks.StalkerClient.get_series_seasons")
    @patch("apps.vod.tasks.StalkerClient.prepare_authenticated_session")
    def test_refresh_series_episodes_skips_disabled_category_relation(
        self,
        mock_prepare_authenticated_session,
        mock_get_series_seasons,
        mock_get_series_episodes,
    ):
        category = VODCategory.objects.create(name="Drama", category_type="series")
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=category,
            enabled=False,
        )
        self.series_relation.category = category
        self.series_relation.save(update_fields=["category"])

        refresh_series_episodes(
            self.account,
            self.series,
            self.external_series_id,
        )

        self.series_relation.refresh_from_db()

        self.assertFalse(self.series_relation.custom_properties["detailed_fetched"])
        self.assertFalse(self.series_relation.custom_properties["episodes_fetched"])
        self.assertEqual(Episode.objects.filter(series=self.series).count(), 0)
        mock_prepare_authenticated_session.assert_not_called()
        mock_get_series_seasons.assert_not_called()
        mock_get_series_episodes.assert_not_called()

    @patch("apps.vod.tasks.StalkerClient.get_series_episodes")
    @patch("apps.vod.tasks.StalkerClient.get_series_seasons")
    @patch("apps.vod.tasks.StalkerClient.prepare_authenticated_session")
    def test_refresh_series_episodes_imports_stalker_episode_rows_idempotently(
        self,
        mock_prepare_authenticated_session,
        mock_get_series_seasons,
        mock_get_series_episodes,
    ):
        mock_get_series_seasons.side_effect = self.get_series_seasons_side_effect
        mock_get_series_episodes.side_effect = self.get_series_episodes_side_effect

        refresh_series_episodes(
            self.account,
            self.series,
            self.external_series_id,
        )

        self.series.refresh_from_db()
        self.series_relation.refresh_from_db()

        self.assertEqual(self.series.description, "Detailed season summary")
        self.assertEqual(self.series.rating, "8.7")
        self.assertEqual(self.series.genre, "Drama")
        self.assertEqual(self.series.year, 2023)
        self.assertEqual(self.series.custom_properties["country"], "South Africa")
        self.assertTrue(self.series_relation.custom_properties["detailed_fetched"])
        self.assertTrue(self.series_relation.custom_properties["episodes_fetched"])
        self.assertEqual(
            self.series_relation.custom_properties["detail_data"]["plot"],
            "Detailed season summary",
        )

        self.assertEqual(Episode.objects.filter(series=self.series).count(), 2)
        self.assertEqual(
            M3UEpisodeRelation.objects.filter(m3u_account=self.account).count(),
            2,
        )

        first_relation = M3UEpisodeRelation.objects.get(stream_id="9001")
        second_relation = M3UEpisodeRelation.objects.get(stream_id="9002")

        self.assertEqual(first_relation.container_extension, "mkv")
        self.assertEqual(first_relation.custom_properties["provider_type"], "stalker")
        self.assertEqual(first_relation.custom_properties["stalker_series_id"], self.external_series_id)
        self.assertEqual(first_relation.custom_properties["stalker_season_id"], "5001")
        self.assertEqual(
            first_relation.custom_properties["cmd"],
            "ffmpeg http://provider.example.com/ep1.mkv",
        )
        self.assertEqual(second_relation.custom_properties["stalker_episode_id"], "9002")

        mock_get_series_seasons.side_effect = [
            [
                {
                    "id": "5001",
                    "title": "Season 1",
                    "plot": "Detailed season summary",
                    "rating": "8.7",
                    "genre": "Drama",
                    "year": "2023",
                },
                {
                    "id": "5002",
                    "title": "Season 2",
                },
            ],
            [],
        ]
        mock_get_series_episodes.side_effect = [
            [
                {
                    "id": "9001",
                    "title": "Episode 1 Updated",
                    "series_number": "1",
                    "plot": "Pilot updated",
                    "rating": "8.2",
                    "cmd": "ffmpeg http://provider.example.com/ep1-updated.mkv",
                }
            ],
            [],
            [
                {
                    "episode_id": "9002",
                    "title": "Episode 1",
                    "episode_number": "1",
                    "plot": "Season two premiere",
                    "rating": "8.4",
                    "cmd": "ffmpeg http://provider.example.com/ep2.mp4",
                }
            ],
            [],
        ]

        refresh_series_episodes(
            self.account,
            self.series,
            self.external_series_id,
        )

        self.assertEqual(Episode.objects.filter(series=self.series).count(), 2)
        self.assertEqual(
            M3UEpisodeRelation.objects.filter(m3u_account=self.account).count(),
            2,
        )

        updated_episode = Episode.objects.get(series=self.series, season_number=1, episode_number=1)
        updated_relation = M3UEpisodeRelation.objects.get(stream_id="9001")
        self.assertEqual(updated_episode.name, "Episode 1 Updated")
        self.assertEqual(updated_episode.description, "Pilot updated")
        self.assertEqual(updated_relation.container_extension, "mkv")
        self.assertEqual(
            updated_relation.custom_properties["cmd"],
            "ffmpeg http://provider.example.com/ep1-updated.mkv",
        )
        mock_prepare_authenticated_session.assert_called()

    @patch("apps.vod.tasks.StalkerClient.get_series_episodes")
    @patch("apps.vod.tasks.StalkerClient.get_series_seasons")
    @patch("apps.vod.tasks.StalkerClient.prepare_authenticated_session")
    def test_refresh_series_episodes_retries_with_season_number_when_id_returns_seasons(
        self,
        mock_prepare_authenticated_session,
        mock_get_series_seasons,
        mock_get_series_episodes,
    ):
        mock_get_series_seasons.side_effect = [
            [
                {"id": "5001", "title": "Season 1"},
                {"id": "5002", "title": "Season 2"},
                {"id": "5003", "title": "Season 3"},
            ],
            [],
        ]

        season_rows = [
            {"id": "5001", "title": "Season 1"},
            {"id": "5002", "title": "Season 2"},
            {"id": "5003", "title": "Season 3"},
        ]

        def get_series_episodes_side_effect(portal_url, series_id, season_id, page=1):
            self.assertEqual(portal_url, self.portal_url)
            self.assertEqual(series_id, self.external_series_id)

            if page != 1:
                return []

            if season_id in {"5001", "5002", "5003"}:
                return season_rows

            if season_id == "1":
                return [
                    {
                        "id": "9101",
                        "title": "Episode 1",
                        "episode_number": "1",
                        "cmd": "ffmpeg http://provider.example.com/s1e1.mp4",
                    },
                    {
                        "id": "9102",
                        "title": "Episode 2",
                        "episode_number": "2",
                        "cmd": "ffmpeg http://provider.example.com/s1e2.mp4",
                    },
                ]

            if season_id == "2":
                return [
                    {
                        "id": "9201",
                        "title": "Episode 1",
                        "episode_number": "1",
                        "cmd": "ffmpeg http://provider.example.com/s2e1.mp4",
                    },
                    {
                        "id": "9202",
                        "title": "Episode 2",
                        "episode_number": "2",
                        "cmd": "ffmpeg http://provider.example.com/s2e2.mp4",
                    },
                ]

            if season_id == "3":
                return [
                    {
                        "id": "9301",
                        "title": "Episode 1",
                        "episode_number": "1",
                        "cmd": "ffmpeg http://provider.example.com/s3e1.mp4",
                    },
                    {
                        "id": "9302",
                        "title": "Episode 2",
                        "episode_number": "2",
                        "cmd": "ffmpeg http://provider.example.com/s3e2.mp4",
                    },
                ]

            return []

        mock_get_series_episodes.side_effect = get_series_episodes_side_effect

        refresh_series_episodes(
            self.account,
            self.series,
            self.external_series_id,
        )

        self.assertEqual(Episode.objects.filter(series=self.series, season_number=1).count(), 2)
        self.assertEqual(Episode.objects.filter(series=self.series, season_number=2).count(), 2)
        self.assertEqual(Episode.objects.filter(series=self.series, season_number=3).count(), 2)
        self.assertEqual(M3UEpisodeRelation.objects.filter(m3u_account=self.account).count(), 6)
        mock_prepare_authenticated_session.assert_called()

    @patch("apps.vod.tasks.StalkerClient.get_series_episodes")
    @patch("apps.vod.tasks.StalkerClient.get_series_seasons")
    @patch("apps.vod.tasks.StalkerClient.prepare_authenticated_session")
    def test_refresh_series_episodes_uses_embedded_episode_numbers_when_portal_never_returns_episode_rows(
        self,
        mock_prepare_authenticated_session,
        mock_get_series_seasons,
        mock_get_series_episodes,
    ):
        mock_get_series_seasons.side_effect = [
            [
                {
                    "id": "7196:1",
                    "title": "Season 1",
                    "series": [1, 2, 3, 4],
                    "cmd": "season-one-cmd",
                },
                {
                    "id": "7196:2",
                    "title": "Season 2",
                    "series": [1, 2],
                    "cmd": "season-two-cmd",
                },
            ],
            [],
        ]
        mock_get_series_episodes.side_effect = [
            [
                {
                    "id": "7196:1",
                    "title": "Season 1",
                    "series": [1, 2, 3, 4],
                    "cmd": "season-one-cmd",
                },
                {
                    "id": "7196:2",
                    "title": "Season 2",
                    "series": [1, 2],
                    "cmd": "season-two-cmd",
                },
            ],
            [],
            [
                {
                    "id": "7196:1",
                    "title": "Season 1",
                    "series": [1, 2, 3, 4],
                    "cmd": "season-one-cmd",
                },
                {
                    "id": "7196:2",
                    "title": "Season 2",
                    "series": [1, 2],
                    "cmd": "season-two-cmd",
                },
            ],
            [],
        ]

        refresh_series_episodes(
            self.account,
            self.series,
            self.external_series_id,
        )

        self.assertEqual(Episode.objects.filter(series=self.series, season_number=1).count(), 4)
        self.assertEqual(Episode.objects.filter(series=self.series, season_number=2).count(), 2)
        self.assertEqual(M3UEpisodeRelation.objects.filter(m3u_account=self.account).count(), 6)
        first_episode = Episode.objects.get(series=self.series, season_number=1, episode_number=1)
        self.assertEqual(first_episode.name, "Episode 1")
        first_relation = M3UEpisodeRelation.objects.get(stream_id="7196:1:1")
        self.assertEqual(first_relation.custom_properties["cmd"], "season-one-cmd")
        self.assertEqual(first_relation.custom_properties["stalker_season_id"], "7196:1")
        mock_prepare_authenticated_session.assert_called()

    def test_process_series_batch_preserves_existing_detail_fetch_flags(self):
        category = VODCategory.objects.create(name="Shows", category_type="series")
        category_relation = M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=category,
            enabled=True,
            custom_properties={
                "stalker_category_id": "20",
                "stalker_category_type": "series",
            },
        )
        self.series_relation.custom_properties = {
            "basic_data": {"id": self.external_series_id, "title": "Fatal Seduction"},
            "detail_data": {"plot": "Detailed season summary"},
            "detailed_fetched": True,
            "episodes_fetched": True,
        }
        self.series_relation.save(update_fields=["custom_properties"])

        process_series_batch(
            self.account,
            [
                {
                    "id": self.external_series_id,
                    "title": "Fatal Seduction",
                    "plot": "Top-level catalog summary",
                    "category_id": "20",
                }
            ],
            {"20": category},
            {category.id: category_relation},
            scan_start_time=timezone.now(),
        )

        self.series_relation.refresh_from_db()
        self.assertTrue(self.series_relation.custom_properties["detailed_fetched"])
        self.assertTrue(self.series_relation.custom_properties["episodes_fetched"])
        self.assertEqual(
            self.series_relation.custom_properties["detail_data"]["plot"],
            "Detailed season summary",
        )
        self.assertEqual(
            self.series_relation.custom_properties["basic_data"]["plot"],
            "Top-level catalog summary",
        )


class StalkerPhase13ProviderInfoApiTests(StalkerPhase13Base):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("apps.vod.tasks.StalkerClient.get_series_episodes")
    @patch("apps.vod.tasks.StalkerClient.get_series_seasons")
    @patch("apps.vod.tasks.StalkerClient.prepare_authenticated_session")
    def test_provider_info_endpoint_loads_stalker_episodes_on_demand(
        self,
        mock_prepare_authenticated_session,
        mock_get_series_seasons,
        mock_get_series_episodes,
    ):
        mock_get_series_seasons.side_effect = self.get_series_seasons_side_effect
        mock_get_series_episodes.side_effect = self.get_series_episodes_side_effect

        response = self.client.get(
            f"/api/vod/series/{self.series.id}/provider-info/?include_episodes=true"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()

        self.assertTrue(payload["detailed_fetched"])
        self.assertTrue(payload["episodes_fetched"])
        self.assertEqual(payload["genre"], "Drama")
        self.assertEqual(payload["episodes"]["1"][0]["title"], "Episode 1")
        self.assertEqual(payload["episodes"]["1"][0]["container_extension"], "mkv")
        self.assertEqual(payload["episodes"]["2"][0]["title"], "Episode 1")
        mock_prepare_authenticated_session.assert_called()

    @patch("apps.vod.tasks.StalkerClient.get_series_episodes")
    @patch("apps.vod.tasks.StalkerClient.get_series_seasons")
    @patch("apps.vod.tasks.StalkerClient.prepare_authenticated_session")
    def test_provider_info_endpoint_rejects_disabled_category_relation(
        self,
        mock_prepare_authenticated_session,
        mock_get_series_seasons,
        mock_get_series_episodes,
    ):
        category = VODCategory.objects.create(name="Disabled Drama", category_type="series")
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=category,
            enabled=False,
        )
        self.series_relation.category = category
        self.series_relation.save(update_fields=["category"])

        response = self.client.get(
            f"/api/vod/series/{self.series.id}/provider-info/?include_episodes=true"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_prepare_authenticated_session.assert_not_called()
        mock_get_series_seasons.assert_not_called()
        mock_get_series_episodes.assert_not_called()


class VODProvidersApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="provider-admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.movie = Movie.objects.create(
            name="Dust Bunny",
            year=2025,
            tmdb_id="1043197",
        )
        self.primary_account = M3UAccount.objects.create(
            name="trexiptv",
            account_type=M3UAccount.Types.XC,
            server_url="http://xc.example.com",
            username="demo1",
            password="secret1",
        )
        self.secondary_account = M3UAccount.objects.create(
            name="onair",
            account_type=M3UAccount.Types.XC,
            server_url="http://xc2.example.com",
            username="demo2",
            password="secret2",
        )

    def test_movie_providers_endpoint_returns_one_relation_per_account(self):
        M3UMovieRelation.objects.create(
            m3u_account=self.primary_account,
            movie=self.movie,
            stream_id="1001",
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.primary_account,
            movie=self.movie,
            stream_id="1002",
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.secondary_account,
            movie=self.movie,
            stream_id="2001",
        )

        response = self.client.get(f"/api/vod/movies/{self.movie.id}/providers/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertCountEqual(
            [item["m3u_account"]["name"] for item in payload],
            ["trexiptv", "onair"],
        )

    def test_movie_providers_endpoint_filters_by_category_context(self):
        fr_category = VODCategory.objects.create(
            name="|FR| FILMS 2026",
            category_type="movie",
        )
        bg_category = VODCategory.objects.create(
            name="|BG| BULGARIA FILMI",
            category_type="movie",
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.primary_account,
            category=fr_category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.primary_account,
            category=bg_category,
            enabled=True,
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.primary_account,
            movie=self.movie,
            category=fr_category,
            stream_id="fr-1001",
            custom_properties={"basic_data": {"title": "FR - Dust Bunny"}},
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.primary_account,
            movie=self.movie,
            category=bg_category,
            stream_id="bg-1002",
            custom_properties={"basic_data": {"title": "BG - Dust Bunny"}},
        )

        response = self.client.get(
            f"/api/vod/movies/{self.movie.id}/providers/",
            {"category": f"{fr_category.name}|{fr_category.category_type}"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["stream_id"], "fr-1001")
        self.assertEqual(payload[0]["category"]["id"], fr_category.id)

    def test_movie_provider_info_endpoint_filters_by_category_context(self):
        fr_category = VODCategory.objects.create(
            name="|FR| FILMS 2026",
            category_type="movie",
        )
        bg_category = VODCategory.objects.create(
            name="|BG| BULGARIA FILMI",
            category_type="movie",
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.primary_account,
            category=fr_category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.primary_account,
            category=bg_category,
            enabled=True,
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.primary_account,
            movie=self.movie,
            category=fr_category,
            stream_id="fr-1001",
            last_advanced_refresh=timezone.now(),
            custom_properties={"basic_data": {"title": "FR - Dust Bunny"}},
        )
        M3UMovieRelation.objects.create(
            m3u_account=self.primary_account,
            movie=self.movie,
            category=bg_category,
            stream_id="bg-1002",
            last_advanced_refresh=timezone.now(),
            custom_properties={"basic_data": {"title": "BG - Dust Bunny"}},
        )

        response = self.client.get(
            f"/api/vod/movies/{self.movie.id}/provider-info/",
            {"category": f"{fr_category.name}|{fr_category.category_type}"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["name"], "FR - Dust Bunny")
        self.assertEqual(payload["stream_id"], "fr-1001")
