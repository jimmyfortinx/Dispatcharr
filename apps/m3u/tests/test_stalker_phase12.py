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

    def test_stalker_category_requests_prefers_all_bucket(self):
        requests = get_stalker_category_requests(
            {
                "*": self.category,
                "10": self.category,
                "__uncategorized__": self.category,
            }
        )

        self.assertEqual(requests, ["*"])


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
