from django.test import RequestFactory, TestCase
from unittest.mock import patch

from apps.m3u.models import M3UAccount
from apps.proxy.vod_proxy import views
from apps.vod.models import M3UMovieRelation, Movie


class FakeProbeRedisPipeline:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.operations = []

    def zadd(self, key, mapping):
        self.operations.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, minimum, maximum):
        self.operations.append(("zremrangebyscore", key, minimum, maximum))
        return self

    def zcard(self, key):
        self.operations.append(("zcard", key))
        return self

    def expire(self, key, seconds):
        self.operations.append(("expire", key, seconds))
        return self

    def execute(self):
        results = []
        for operation in self.operations:
            action = operation[0]
            if action == "zadd":
                _, key, mapping = operation
                bucket = self.redis_client.sorted_sets.setdefault(key, {})
                bucket.update(mapping)
                results.append(1)
            elif action == "zremrangebyscore":
                _, key, _, maximum = operation
                bucket = self.redis_client.sorted_sets.setdefault(key, {})
                cutoff = float(maximum)
                removed = [
                    member for member, score in bucket.items()
                    if score <= cutoff
                ]
                for member in removed:
                    bucket.pop(member, None)
                results.append(len(removed))
            elif action == "zcard":
                _, key = operation
                results.append(len(self.redis_client.sorted_sets.get(key, {})))
            elif action == "expire":
                results.append(True)
        self.operations.clear()
        return results


class FakeProbeRedis:
    def __init__(self):
        self.sorted_sets = {}

    def pipeline(self, transaction=False):
        return FakeProbeRedisPipeline(self)


class TestProbeModeHeuristics(TestCase):
    def setUp(self):
        views._probe_activity.clear()
        self.redis_client = FakeProbeRedis()
        self.redis_patcher = patch(
            "apps.proxy.vod_proxy.views.RedisClient.get_client",
            return_value=self.redis_client,
        )
        self.redis_patcher.start()

    def tearDown(self):
        views._probe_activity.clear()
        self.redis_patcher.stop()

    def test_probe_mode_requires_scan_burst(self):
        user_agent = "Lavf/60.16.100"
        kwargs = {
            "client_ip": "10.0.0.10",
            "client_user_agent": user_agent,
            "range_header": "bytes=0-",
            "session_id": None,
            "offset": None,
            "utc_start": None,
            "utc_end": None,
        }

        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-1",
                **kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-2",
                **kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-3",
                **kwargs,
            )
        )

    def test_probe_mode_accepts_non_zero_open_ended_ranges_during_burst(self):
        user_agent = "Lavf/60.16.100"
        kwargs = {
            "client_ip": "10.0.0.10",
            "client_user_agent": user_agent,
            "range_header": "bytes=1825399766-",
            "session_id": None,
            "offset": None,
            "utc_start": None,
            "utc_end": None,
        }

        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-1",
                **kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-2",
                **kwargs,
            )
        )
        self.assertTrue(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-3",
                **kwargs,
            )
        )

    def test_movie_initial_range_stays_real_even_during_scan_burst(self):
        user_agent = "Lavf/60.16.100"

        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent=user_agent,
                content_type="episode",
                content_id="episode-1",
                range_header="bytes=0-",
                session_id=None,
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent=user_agent,
                content_type="episode",
                content_id="episode-2",
                range_header="bytes=0-",
                session_id=None,
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )

        evaluation = views._evaluate_probe_mode(
            client_ip="10.0.0.10",
            client_user_agent=user_agent,
            content_type="movie",
            content_id="movie-vicious",
            range_header="bytes=0-",
            session_id=None,
            offset=None,
            utc_start=None,
            utc_end=None,
        )

        self.assertFalse(evaluation["enabled"])
        self.assertEqual(evaluation["reason"], "movie-initial-range-creates-session")

    def test_episode_initial_range_still_uses_probe_mode_during_scan_burst(self):
        user_agent = "Lavf/60.16.100"
        kwargs = {
            "client_ip": "10.0.0.10",
            "client_user_agent": user_agent,
            "range_header": "bytes=0-",
            "session_id": None,
            "offset": None,
            "utc_start": None,
            "utc_end": None,
        }

        self.assertFalse(
            views._should_use_probe_mode(
                content_type="episode",
                content_id="episode-1",
                **kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                content_type="episode",
                content_id="episode-2",
                **kwargs,
            )
        )
        self.assertTrue(
            views._should_use_probe_mode(
                content_type="episode",
                content_id="episode-3",
                **kwargs,
            )
        )

    def test_probe_mode_rejects_non_probe_shapes(self):
        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent="Mozilla/5.0",
                content_type="movie",
                content_id="movie-1",
                range_header="bytes=0-",
                session_id=None,
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )

    def test_probe_activity_is_shared_through_redis(self):
        user_agent = "Lavf/60.16.100"
        activity_key = views._get_probe_activity_redis_key("10.0.0.10", user_agent)
        first_worker_kwargs = {
            "client_ip": "10.0.0.10",
            "client_user_agent": user_agent,
            "range_header": "bytes=0-",
            "session_id": None,
            "offset": None,
            "utc_start": None,
            "utc_end": None,
        }

        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-1",
                **first_worker_kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-2",
                **first_worker_kwargs,
            )
        )

        self.assertEqual(
            len(self.redis_client.sorted_sets.get(activity_key, {})),
            2,
        )

        self.assertTrue(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-3",
                **first_worker_kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent="Lavf/60.16.100",
                content_type="movie",
                content_id="movie-1",
                range_header="bytes=100-200",
                session_id=None,
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent="Lavf/60.16.100",
                content_type="movie",
                content_id="movie-1",
                range_header="bytes=0-",
                session_id="vod_existing",
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )


class TestProbeModeSyntheticResponse(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = M3UAccount.objects.create(
            name="probe-account",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": True},
        )
        self.movie = Movie.objects.create(
            name="Probe Movie",
            duration_secs=5400,
        )
        self.relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.movie,
            stream_id="probe-movie-1",
            container_extension="mkv",
            custom_properties={
                "detailed_info": {
                    "bitrate": 8000,
                }
            },
        )

    @patch("apps.proxy.vod_proxy.views.network_access_allowed", return_value=True)
    @patch("apps.proxy.vod_proxy.views.resolve_vod_stream_context")
    @patch.object(views.VODStreamView, "_get_m3u_profile")
    @patch.object(views.VODStreamView, "_get_content_and_relation")
    @patch(
        "apps.proxy.vod_proxy.views._evaluate_probe_mode",
        return_value={
            "enabled": True,
            "reason": "scan-burst-detected",
            "backend": "memory",
            "unique_content_count": 3,
        },
    )
    def test_probe_mode_uses_synthetic_local_response_without_upstream_resolution(
        self,
        _mock_probe_eval,
        mock_get_content_and_relation,
        mock_get_m3u_profile,
        mock_resolve_vod_stream_context,
        _mock_network_access_allowed,
    ):
        mock_get_content_and_relation.return_value = (self.movie, self.relation)

        request = self.factory.get(
            f"/proxy/vod/movie/{self.movie.uuid}",
            HTTP_USER_AGENT="Plex Media Server",
            HTTP_RANGE="bytes=0-",
        )

        response = views.VODStreamView().get(
            request,
            "movie",
            self.movie.uuid,
            None,
            None,
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["X-Dispatcharr-Probe-Mode"], "1")
        self.assertEqual(response["X-Dispatcharr-Probe-Synthetic"], "1")
        self.assertEqual(response["Content-Type"], "video/x-matroska")
        self.assertTrue(response.content.startswith(b"\x1A\x45\xDF\xA3"))
        mock_resolve_vod_stream_context.assert_not_called()
        mock_get_m3u_profile.assert_not_called()

    @patch("apps.proxy.vod_proxy.views.network_access_allowed", return_value=True)
    @patch("apps.proxy.vod_proxy.views.resolve_vod_stream_context")
    @patch.object(views.VODStreamView, "_get_m3u_profile")
    @patch.object(views.VODStreamView, "_get_content_and_relation")
    def test_head_uses_synthetic_local_response_for_stalker_plex_scan_requests(
        self,
        mock_get_content_and_relation,
        mock_get_m3u_profile,
        mock_resolve_vod_stream_context,
        _mock_network_access_allowed,
    ):
        mock_get_content_and_relation.return_value = (self.movie, self.relation)

        request = self.factory.head(
            f"/proxy/vod/movie/{self.movie.uuid}",
            HTTP_USER_AGENT="Lavf/60.16.100",
        )

        response = views.VODStreamView().head(
            request,
            "movie",
            self.movie.uuid,
            None,
            None,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Dispatcharr-Probe-Mode"], "1")
        self.assertEqual(response["X-Dispatcharr-Probe-Synthetic"], "1")
        self.assertEqual(response["Content-Type"], "video/x-matroska")
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(response["Content-Length"], "5400000000")
        self.assertIn("/proxy/vod/movie/", response["X-Session-URL"])
        self.assertTrue(response["X-Dispatcharr-Session"].startswith("vod_"))
        mock_resolve_vod_stream_context.assert_not_called()
        mock_get_m3u_profile.assert_not_called()

    def test_mp4_probe_payload_is_a_parseable_container_sample(self):
        payload = views._get_synthetic_probe_header_bytes("mp4")

        self.assertTrue(payload.startswith(b"\x00\x00\x00\x20ftypisom"))
        self.assertIn(b"moov", payload)
        self.assertIn(b"mdat", payload)
