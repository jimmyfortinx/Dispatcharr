from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.vod.models import (
    Episode,
    M3UEpisodeRelation,
    M3USeriesRelation,
    M3UVODCategoryRelation,
    Series,
    VODCategory,
)
from apps.vod.tasks import process_series_batch, refresh_series_episodes


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


class StalkerPhase13SeriesImportTests(StalkerPhase13Base):
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
