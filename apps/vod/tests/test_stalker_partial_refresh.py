"""
A Stalker VOD refresh must survive a category that keeps failing.

Before, one failed page aborted the whole refresh. Now a failed page is
retried (with a new token on auth errors), a category that still fails is
skipped, and cleanup keeps that category's existing relations.
"""
import threading
from datetime import timedelta
from unittest.mock import ANY, patch

from django.test import TestCase
from django.utils import timezone

from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerClient, StalkerError, StalkerRecoverableError
from apps.vod.models import (
    M3UMovieRelation,
    M3USeriesRelation,
    M3UVODCategoryRelation,
    Movie,
    Series,
    VODCategory,
)
from apps.vod.tasks import (
    StalkerCatalogFetchReport,
    build_stalker_failed_category_filter,
    cleanup_orphaned_vod_content,
    iter_stalker_catalog_batches,
    refresh_vod_content,
)

PORTAL_URL = "http://portal.example.com/stalker_portal/server/load.php"
NO_RETRY_DELAY = patch("apps.vod.tasks.STALKER_CATALOG_RETRY_DELAYS", (0, 0))


class SharedPortalState:
    def __init__(self, responses):
        self.responses = responses
        self.lock = threading.Lock()
        self.calls = []
        self.auth_calls = []


class FakeCatalogClient:
    """Clones like StalkerClient: each clone has its own token.

    A callable response is called with the worker client to get the page.
    """

    def __init__(self, state, token="OLD"):
        self.state = state
        self.token = token
        self.vod_portal_url = PORTAL_URL

    def clone_for_parallel_catalog(self):
        return FakeCatalogClient(self.state, token=self.token)

    def get_vod_movies(self, portal_url, category_id=None, page=1):
        key = (str(category_id), page)
        with self.state.lock:
            self.state.calls.append(key)
        response = self.state.responses.get(key, [])
        if callable(response):
            return response(self)
        return response

    def prepare_authenticated_session(self, portal_url, force=False):
        with self.state.lock:
            self.state.auth_calls.append((portal_url, force))
        self.token = "NEW"


def always_fail(client):
    raise StalkerError("Request failed: 502 Bad Gateway")


def page_of(*ids):
    return [{"id": str(item_id), "title": f"Title {item_id}"} for item_id in ids]


def collect_ids(batches):
    return sorted(item["id"] for batch in batches for item in batch)


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

    def _collect(self, client, report):
        return list(
            iter_stalker_catalog_batches(
                client,
                self.account,
                self.categories_by_provider,
                self.relations,
                content_type="movie",
                report=report,
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
                state = SharedPortalState(
                    {("10", 1): page_of(100), ("11", 1): always_fail}
                )
                report = StalkerCatalogFetchReport()

                batches = self._collect(FakeCatalogClient(state), report)

                self.assertEqual(collect_ids(batches), ["100"])
                self.assertEqual(report.failed_provider_category_ids, {"11"})
                self.assertEqual(report.requested_count, 2)
                self.assertFalse(report.all_failed)
                # One first attempt plus two retries for the failing page.
                self.assertEqual(state.calls.count(("11", 1)), 3)

    @NO_RETRY_DELAY
    def test_late_failure_keeps_earlier_pages_and_drains_other_producers(self):
        # Category 10 yields more pages than a producer queue holds (4) while
        # category 11 fails after two populated pages. The fetch must finish.
        self._set_workers(2)
        responses = {("10", page): page_of(1000 + page) for page in range(1, 9)}
        responses[("11", 1)] = page_of(2001)
        responses[("11", 2)] = page_of(2002)
        responses[("11", 3)] = always_fail
        state = SharedPortalState(responses)
        report = StalkerCatalogFetchReport()
        outcome = {}

        def run():
            outcome["batches"] = self._collect(FakeCatalogClient(state), report)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "catalog fetch did not finish")
        self.assertEqual(
            collect_ids(outcome["batches"]),
            sorted([str(1000 + page) for page in range(1, 9)] + ["2001", "2002"]),
        )
        self.assertEqual(report.failed_provider_category_ids, {"11"})

    @NO_RETRY_DELAY
    def test_every_category_failing_is_reported(self):
        state = SharedPortalState({("10", 1): always_fail, ("11", 1): always_fail})
        report = StalkerCatalogFetchReport()

        self.assertEqual(self._collect(FakeCatalogClient(state), report), [])
        self.assertTrue(report.all_failed)
        self.assertIsInstance(report.last_error, StalkerError)

    @NO_RETRY_DELAY
    def test_transient_page_failure_is_retried(self):
        attempts = {"count": 0}

        def fail_once(client):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise StalkerError("Request failed: read timeout")
            return page_of(200)

        self._set_workers(1)
        state = SharedPortalState({("11", 1): fail_once})
        report = StalkerCatalogFetchReport()

        batches = self._collect(FakeCatalogClient(state), report)

        self.assertEqual(collect_ids(batches), ["200"])
        self.assertEqual(report.failed_provider_category_ids, set())
        self.assertEqual(state.auth_calls, [])

    @NO_RETRY_DELAY
    def test_expired_token_is_renewed_once_and_shared_with_workers(self):
        both_failed = threading.Barrier(2, timeout=5)

        def needs_valid_token(item_id):
            def respond(worker_client):
                if worker_client.token == "NEW":
                    return page_of(item_id)
                # Both workers fail on the old token at the same time. A retry
                # that still used the old token would break the barrier.
                both_failed.wait()
                raise StalkerRecoverableError("Request failed: 401 Unauthorized")

            return respond

        self._set_workers(2)
        state = SharedPortalState(
            {("10", 1): needs_valid_token(100), ("11", 1): needs_valid_token(200)}
        )
        parent = FakeCatalogClient(state)
        report = StalkerCatalogFetchReport()

        batches = self._collect(parent, report)

        self.assertEqual(collect_ids(batches), ["100", "200"])
        self.assertEqual(state.auth_calls, [(PORTAL_URL, True)])
        self.assertEqual(parent.token, "NEW")
        self.assertEqual(report.failed_provider_category_ids, set())


class StalkerClientForcedAuthTests(TestCase):
    def test_force_reauthenticates_a_prepared_session(self):
        client = StalkerClient(server_url=PORTAL_URL, mac="00:1A:79:00:00:94")
        client.token = "OLD"
        client._prepared_portal_url = PORTAL_URL

        with patch.object(client, "handshake") as handshake, patch.object(
            client, "_authenticate_portal"
        ) as authenticate:
            client.prepare_authenticated_session(PORTAL_URL)
            handshake.assert_not_called()

            client.prepare_authenticated_session(PORTAL_URL, force=True)

        handshake.assert_called_once_with(PORTAL_URL)
        authenticate.assert_called_once_with(PORTAL_URL)


class CleanupKeepsFailedCategoriesTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Cleanup Keep",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:92", "enable_vod": True},
        )
        self.scan_start = timezone.now()
        self.stale_seen = self.scan_start - timedelta(days=1)

    def _movie_relation(self, stream_id, category=None, basic_data=None):
        return M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=Movie.objects.create(name=f"Movie {stream_id}"),
            category=category,
            stream_id=stream_id,
            custom_properties={"basic_data": basic_data or {}},
            last_seen=self.stale_seen,
        )

    def _report(self, *provider_ids):
        return StalkerCatalogFetchReport(
            requested_count=len(provider_ids) + 1,
            failed_provider_category_ids=set(provider_ids),
        )

    def test_relations_of_failed_categories_are_not_stale(self):
        failed_movies = VODCategory.objects.create(name="Failed", category_type="movie")
        fetched_movies = VODCategory.objects.create(name="Fetched", category_type="movie")
        failed_series = VODCategory.objects.create(name="Failed", category_type="series")

        kept_movie = self._movie_relation("1", category=failed_movies)
        removed_movie = self._movie_relation("2", category=fetched_movies)
        # No category and no saved provider id: still cleaned up as before.
        removed_bare_movie = self._movie_relation("4")
        kept_series = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=Series.objects.create(name="Kept Series"),
            category=failed_series,
            external_series_id="3",
            last_seen=self.stale_seen,
        )

        cleanup_orphaned_vod_content(
            account_id=self.account.id,
            scan_start_time=self.scan_start,
            keep_movie_relations=build_stalker_failed_category_filter(
                self._report("10"), {"10": failed_movies, "11": fetched_movies},
            ),
            keep_series_relations=build_stalker_failed_category_filter(
                self._report("10"), {"10": failed_series},
            ),
        )

        self.assertTrue(M3UMovieRelation.objects.filter(id=kept_movie.id).exists())
        self.assertTrue(M3USeriesRelation.objects.filter(id=kept_series.id).exists())
        self.assertFalse(M3UMovieRelation.objects.filter(id=removed_movie.id).exists())
        self.assertFalse(
            M3UMovieRelation.objects.filter(id=removed_bare_movie.id).exists()
        )

    def test_relations_keep_their_failed_provider_category_after_a_rename(self):
        # Provider category 10 was renamed: category sync created "Action HD"
        # and the old "Action" row is gone, so the relation lost its category.
        renamed = VODCategory.objects.create(name="Action HD", category_type="movie")
        kept_movie = self._movie_relation(
            "1", basic_data={"_requested_category_id": "10"}
        )
        removed_movie = self._movie_relation(
            "2", basic_data={"_requested_category_id": "11"}
        )

        cleanup_orphaned_vod_content(
            account_id=self.account.id,
            scan_start_time=self.scan_start,
            keep_movie_relations=build_stalker_failed_category_filter(
                self._report("10"), {"10": renamed},
            ),
        )

        self.assertTrue(M3UMovieRelation.objects.filter(id=kept_movie.id).exists())
        self.assertFalse(M3UMovieRelation.objects.filter(id=removed_movie.id).exists())


@patch("apps.m3u.tasks.send_m3u_update")
@patch("apps.vod.tasks.release_task_lock")
@patch("apps.vod.tasks.acquire_task_lock", return_value=True)
@patch("apps.vod.tasks.cleanup_orphaned_vod_content", return_value="cleanup complete")
@patch("apps.vod.tasks.refresh_series")
@patch("apps.vod.tasks.refresh_movies")
@patch("apps.vod.tasks.refresh_categories")
class RefreshVodContentPartialFailureTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Refresh Warning",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:93", "enable_vod": True},
        )
        self.kids = VODCategory.objects.create(name="Kids", category_type="movie")
        self.shows = VODCategory.objects.create(name="Shows", category_type="series")

    def _failed_report(self, *provider_ids, requested_count):
        return StalkerCatalogFetchReport(
            requested_count=requested_count,
            failed_provider_category_ids=set(provider_ids),
            last_error=StalkerError("Request failed: 502 Bad Gateway"),
        )

    def test_all_movie_categories_failing_still_imports_series(
        self,
        mock_refresh_categories,
        mock_refresh_movies,
        mock_refresh_series,
        mock_cleanup,
        _mock_acquire,
        _mock_release,
        mock_send_m3u_update,
    ):
        mock_refresh_categories.return_value = ({"10": self.kids}, {"20": self.shows})
        mock_refresh_movies.return_value = self._failed_report("10", requested_count=1)
        mock_refresh_series.return_value = StalkerCatalogFetchReport(requested_count=1)

        result = refresh_vod_content(self.account.id)

        mock_refresh_series.assert_called_once()
        mock_cleanup.assert_called_once_with(
            account_id=self.account.id,
            scan_start_time=ANY,
            keep_movie_relations=ANY,
            keep_series_relations=None,
        )
        self.assertIsNotNone(mock_cleanup.call_args.kwargs["keep_movie_relations"])
        final_update = mock_send_m3u_update.call_args_list[-1]
        self.assertEqual(final_update.kwargs["status"], "warning")
        self.assertIn("failed to load: Kids", final_update.kwargs["message"])
        self.assertIn("Kids", result)

    def test_refresh_fails_when_every_category_fails(
        self,
        mock_refresh_categories,
        mock_refresh_movies,
        mock_refresh_series,
        mock_cleanup,
        _mock_acquire,
        _mock_release,
        mock_send_m3u_update,
    ):
        mock_refresh_categories.return_value = ({"10": self.kids}, {"20": self.shows})
        mock_refresh_movies.return_value = self._failed_report("10", requested_count=1)
        mock_refresh_series.return_value = self._failed_report("20", requested_count=1)

        result = refresh_vod_content(self.account.id)

        mock_cleanup.assert_not_called()
        self.assertIn("VOD refresh failed", result)
        self.assertEqual(mock_send_m3u_update.call_args_list[-1].kwargs["status"], "error")
