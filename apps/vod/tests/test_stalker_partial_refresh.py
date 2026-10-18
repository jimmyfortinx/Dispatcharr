"""
A Stalker VOD refresh must survive a category that keeps failing.

Before, one failed page aborted the whole refresh. Now a failed page is
retried (with a new token on auth errors), a category that still fails is
skipped, and cleanup keeps that category's existing relations.
"""
from datetime import timedelta
from unittest.mock import ANY, patch

from django.test import TestCase
from django.utils import timezone

from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerError, StalkerRecoverableError
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
    iter_stalker_catalog_batches,
    refresh_vod_content,
)

PORTAL_URL = "http://portal.example.com/stalker_portal/server/load.php"
NO_RETRY_DELAY = patch("apps.vod.tasks.STALKER_CATALOG_RETRY_DELAYS", (0, 0))


class FakeCatalogClient:
    """Returns canned pages; a callable response is called to get the page."""

    def __init__(self, responses, token="OLD"):
        self.responses = responses
        self.token = token
        self.vod_portal_url = PORTAL_URL
        self.calls = []
        self.auth_calls = []

    def clone_for_parallel_catalog(self):
        # Sharing one instance keeps call and token bookkeeping in one place.
        return self

    def get_vod_movies(self, portal_url, category_id=None, page=1):
        key = (str(category_id), page)
        self.calls.append(key)
        response = self.responses.get(key, [])
        if callable(response):
            return response(self)
        return response

    def prepare_authenticated_session(self, portal_url, force=False):
        self.auth_calls.append((portal_url, force))
        self.token = "NEW"


def always_fail(client):
    raise StalkerError("Request failed: 502 Bad Gateway")


class StalkerCatalogPartialFailureTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Partial",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:91", "enable_vod": True},
        )
        self.action = VODCategory.objects.create(name="Action", category_type="movie")
        self.drama = VODCategory.objects.create(name="Drama", category_type="movie")
        self.relations = {
            category.id: M3UVODCategoryRelation.objects.create(
                m3u_account=self.account,
                category=category,
                enabled=True,
            )
            for category in (self.action, self.drama)
        }
        self.categories_by_provider = {"10": self.action, "11": self.drama}

    def _collect(self, client, failed_category_ids):
        return list(
            iter_stalker_catalog_batches(
                client,
                self.account,
                self.categories_by_provider,
                self.relations,
                content_type="movie",
                failed_category_ids=failed_category_ids,
            )
        )

    def _set_workers(self, workers):
        self.account.custom_properties = {
            **self.account.custom_properties,
            "stalker_vod_catalog_workers": workers,
        }
        self.account.save(update_fields=["custom_properties"])

    @NO_RETRY_DELAY
    def test_failed_category_is_skipped_and_reported(self):
        for workers in (1, 2):
            with self.subTest(workers=workers):
                self._set_workers(workers)
                client = FakeCatalogClient(
                    {
                        ("10", 1): [{"id": "100", "title": "Heat"}],
                        ("11", 1): always_fail,
                    }
                )
                failed = set()

                batches = self._collect(client, failed)

                self.assertEqual([item["id"] for batch in batches for item in batch], ["100"])
                self.assertEqual(failed, {"11"})
                # One first attempt plus two retries for the failing page.
                self.assertEqual(client.calls.count(("11", 1)), 3)

    @NO_RETRY_DELAY
    def test_raises_when_every_category_fails(self):
        client = FakeCatalogClient({("10", 1): always_fail, ("11", 1): always_fail})

        with self.assertRaises(StalkerError):
            self._collect(client, set())

    @NO_RETRY_DELAY
    def test_transient_page_failure_is_retried(self):
        attempts = {"count": 0}

        def fail_once(client):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise StalkerError("Request failed: read timeout")
            return [{"id": "200", "title": "Seven"}]

        self._set_workers(1)
        client = FakeCatalogClient({("10", 1): [], ("11", 1): fail_once})
        failed = set()

        batches = self._collect(client, failed)

        self.assertEqual([item["id"] for batch in batches for item in batch], ["200"])
        self.assertEqual(failed, set())
        self.assertEqual(client.auth_calls, [])

    @NO_RETRY_DELAY
    def test_auth_failure_gets_a_new_token_before_retrying(self):
        def needs_new_token(client):
            if client.token != "NEW":
                raise StalkerRecoverableError("Request failed: 401 Unauthorized")
            return [{"id": "300", "title": "Ronin"}]

        self._set_workers(1)
        client = FakeCatalogClient({("10", 1): [], ("11", 1): needs_new_token})
        failed = set()

        batches = self._collect(client, failed)

        self.assertEqual([item["id"] for batch in batches for item in batch], ["300"])
        self.assertEqual(client.auth_calls, [(PORTAL_URL, True)])
        self.assertEqual(client.token, "NEW")
        self.assertEqual(failed, set())


class CleanupKeepsFailedCategoriesTests(TestCase):
    def test_relations_in_kept_categories_are_not_stale(self):
        account = M3UAccount.objects.create(
            name="Stalker Cleanup Keep",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:92", "enable_vod": True},
        )
        failed_movies = VODCategory.objects.create(name="Failed", category_type="movie")
        fetched_movies = VODCategory.objects.create(name="Fetched", category_type="movie")
        failed_series = VODCategory.objects.create(name="Failed", category_type="series")
        scan_start = timezone.now()
        stale_seen = scan_start - timedelta(days=1)

        kept_movie = M3UMovieRelation.objects.create(
            m3u_account=account,
            movie=Movie.objects.create(name="Kept"),
            category=failed_movies,
            stream_id="1",
            last_seen=stale_seen,
        )
        removed_movie = M3UMovieRelation.objects.create(
            m3u_account=account,
            movie=Movie.objects.create(name="Removed"),
            category=fetched_movies,
            stream_id="2",
            last_seen=stale_seen,
        )
        kept_series = M3USeriesRelation.objects.create(
            m3u_account=account,
            series=Series.objects.create(name="Kept Series"),
            category=failed_series,
            external_series_id="3",
            last_seen=stale_seen,
        )

        cleanup_orphaned_vod_content(
            account_id=account.id,
            scan_start_time=scan_start,
            keep_category_ids={failed_movies.id, failed_series.id},
        )

        self.assertTrue(M3UMovieRelation.objects.filter(id=kept_movie.id).exists())
        self.assertTrue(M3USeriesRelation.objects.filter(id=kept_series.id).exists())
        self.assertFalse(M3UMovieRelation.objects.filter(id=removed_movie.id).exists())


class RefreshVodContentPartialFailureTests(TestCase):
    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.vod.tasks.release_task_lock")
    @patch("apps.vod.tasks.acquire_task_lock", return_value=True)
    @patch("apps.vod.tasks.cleanup_orphaned_vod_content", return_value="cleanup complete")
    @patch("apps.vod.tasks.refresh_series")
    @patch("apps.vod.tasks.refresh_movies")
    @patch("apps.vod.tasks.refresh_categories")
    def test_failed_categories_are_kept_and_reported_as_warning(
        self,
        mock_refresh_categories,
        mock_refresh_movies,
        mock_refresh_series,
        mock_cleanup,
        _mock_acquire,
        _mock_release,
        mock_send_m3u_update,
    ):
        account = M3UAccount.objects.create(
            name="Stalker Refresh Warning",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:93", "enable_vod": True},
        )
        failed = VODCategory.objects.create(name="Kids", category_type="movie")
        mock_refresh_categories.return_value = ({"10": failed}, {})
        mock_refresh_movies.return_value = {failed.id}
        mock_refresh_series.return_value = set()

        result = refresh_vod_content(account.id)

        mock_cleanup.assert_called_once_with(
            account_id=account.id,
            scan_start_time=ANY,
            keep_category_ids={failed.id},
        )
        final_update = mock_send_m3u_update.call_args_list[-1]
        self.assertEqual(final_update.kwargs["status"], "warning")
        self.assertIn("failed to load: Kids", final_update.kwargs["message"])
        self.assertIn("Kids", result)
