from django.test import TestCase
from unittest.mock import patch

from apps.proxy.vod_proxy import views


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
        self.assertTrue(
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
