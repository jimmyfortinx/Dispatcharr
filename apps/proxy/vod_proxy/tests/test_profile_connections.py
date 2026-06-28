"""
Tests for VOD proxy profile connection counter fixes.

Covers three race conditions in multi_worker_connection_manager:
  1. decrement_active_streams() return value was ignored — counter stuck on lock contention
  2. Non-atomic GET-then-DECR in _decrement_profile_connections() — counter could go negative
  3. has_active_streams() read without lock — race between decrement and check
"""

from unittest.mock import MagicMock, patch, call
from django.test import TestCase


class FakeRedis:
    """Minimal in-memory Redis stand-in for counter tests."""

    def __init__(self):
        self._data = {}

    def get(self, key):
        val = self._data.get(key)
        return str(val).encode() if val is not None else None

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    def incr(self, key):
        self._data[key] = int(self._data.get(key, 0)) + 1
        return self._data[key]

    def decr(self, key):
        self._data[key] = int(self._data.get(key, 0)) - 1
        return self._data[key]

    def delete(self, key):
        self._data.pop(key, None)

    def exists(self, key):
        return key in self._data

    def pipeline(self):
        return FakePipeline(self)

    def hset(self, key, mapping):
        current = self._data.get(key, {})
        if not isinstance(current, dict):
            current = {}
        current.update(mapping)
        self._data[key] = current
        return True

    def hgetall(self, key):
        value = self._data.get(key, {})
        return value if isinstance(value, dict) else {}

    def expire(self, key, seconds):
        return True

    def scan(self, cursor, match=None, count=None):
        keys = []
        if match == "vod_persistent_connection:*":
            prefix = "vod_persistent_connection:"
            keys = [k for k in self._data.keys() if isinstance(k, str) and k.startswith(prefix)]
        return 0, keys


class FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._cmds = []

    def incr(self, key):
        self._cmds.append(('incr', key))
        return self

    def decr(self, key):
        self._cmds.append(('decr', key))
        return self

    def delete(self, key):
        self._cmds.append(('delete', key))
        return self

    def execute(self):
        results = []
        for cmd, key in self._cmds:
            results.append(getattr(self._redis, cmd)(key))
        self._cmds = []
        return results


class MultiWorkerManagerImportMixin:
    """Mixin to import the manager class with patched Django/Redis deps."""

    @classmethod
    def get_manager_class(cls):
        import importlib
        import sys

        # Stub out heavy Django deps so we can import the module standalone
        for mod in ['apps.vod.models', 'apps.m3u.models', 'core.utils']:
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()

        from apps.proxy.vod_proxy.multi_worker_connection_manager import (
            MultiWorkerVODConnectionManager,
            RedisBackedVODConnection,
        )
        return MultiWorkerVODConnectionManager, RedisBackedVODConnection


class TestDecrementProfileConnectionsAtomic(TestCase):
    """Bug 2: _decrement_profile_connections must be atomic (no GET-then-DECR)."""

    def _make_manager(self, redis):
        _, _ = MultiWorkerManagerImportMixin.get_manager_class()
        from apps.proxy.vod_proxy.multi_worker_connection_manager import MultiWorkerVODConnectionManager
        mgr = MultiWorkerVODConnectionManager.__new__(MultiWorkerVODConnectionManager)
        mgr.redis_client = redis
        mgr.worker_id = 'test-worker'
        return mgr

    def test_decrement_does_not_go_negative(self):
        """Counter must be clamped to 0, never go negative."""
        redis = FakeRedis()
        redis.set('profile_connections:1', 0)
        mgr = self._make_manager(redis)

        result = mgr._decrement_profile_connections(1)

        self.assertEqual(result, 0)
        self.assertEqual(int(redis._data.get('profile_connections:1', 0)), 0)

    def test_decrement_from_one_reaches_zero(self):
        """Normal single decrement should reach 0."""
        redis = FakeRedis()
        redis.set('profile_connections:1', 1)
        mgr = self._make_manager(redis)

        result = mgr._decrement_profile_connections(1)

        self.assertEqual(result, 0)

    def test_concurrent_decrements_clamp_to_zero(self):
        """Two concurrent decrements of a counter at 1 must not leave it at -1."""
        redis = FakeRedis()
        redis.set('profile_connections:1', 1)
        mgr = self._make_manager(redis)

        # Simulate two concurrent decrements (both fire before either reads back)
        mgr._decrement_profile_connections(1)
        mgr._decrement_profile_connections(1)

        final = int(redis._data.get('profile_connections:1', 0))
        self.assertGreaterEqual(final, 0, "Counter must not go negative after concurrent decrements")


class TestLocalTailProbeShortCircuit(TestCase):
    def test_detects_plex_tail_probe_on_existing_session(self):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import (
            _should_serve_existing_session_tail_probe_locally,
        )

        state = MagicMock()
        state.content_length = "4067413760"
        state.active_streams = 1

        self.assertTrue(
            _should_serve_existing_session_tail_probe_locally(
                state,
                "bytes=4067411510-",
                "Lavf/60.16.100",
            )
        )

    def test_builds_local_tail_probe_response(self):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import (
            _build_local_existing_session_tail_probe_response,
        )

        state = MagicMock()
        state.content_length = "4067413760"
        state.content_type = "video/x-matroska"

        response = _build_local_existing_session_tail_probe_response(
            state=state,
            range_header="bytes=4067411510-",
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Type"], "video/x-matroska")
        self.assertEqual(response["Content-Length"], "2048")
        self.assertEqual(
            response["Content-Range"],
            "bytes 4067411510-4067413557/4067413760",
        )
        self.assertEqual(response["X-Dispatcharr-Local-Tail-Probe"], "1")


class TestDecrementActiveStreamsAndCheck(TestCase):
    """Bug 1 & 3: decrement_active_streams_and_check() must be atomic."""

    def _make_connection(self, redis, session_id='test-session'):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import RedisBackedVODConnection
        conn = RedisBackedVODConnection.__new__(RedisBackedVODConnection)
        conn.session_id = session_id
        conn.redis_client = redis
        conn.connection_key = f'vod_connection:{session_id}'
        conn.lock_key = f'vod_lock:{session_id}'
        conn.local_session = None
        conn._lock_acquired = False
        return conn

    def _make_state(self, active_streams=1, profile_id=7):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import SerializableConnectionState
        state = SerializableConnectionState.__new__(SerializableConnectionState)
        state.session_id = 'test-session'
        state.stream_url = 'http://example.com/stream.mkv'
        state.headers = {}
        state.m3u_profile_id = profile_id
        state.active_streams = active_streams
        state.last_activity = 0
        state.worker_id = 'test-worker'
        state.content_type = None
        state.content_length = None
        state.final_url = None
        state.request_count = 0
        state.bytes_sent = 0
        state.content_obj_type = None
        state.content_uuid = None
        state.content_name = None
        state.client_ip = None
        state.client_user_agent = None
        state.utc_start = None
        state.utc_end = None
        state.offset = None
        state.connection_type = 'redis'
        state.created_at = 0
        return state

    def test_returns_success_and_no_remaining_when_last_stream(self):
        """When active_streams goes 1->0, should return (True, False)."""
        from apps.proxy.vod_proxy.multi_worker_connection_manager import RedisBackedVODConnection
        conn = MagicMock(spec=RedisBackedVODConnection)
        conn.session_id = 'test'

        state = MagicMock()
        state.active_streams = 1

        conn._acquire_lock.return_value = True
        conn._get_connection_state.return_value = state
        conn._save_connection_state.return_value = True
        conn._release_lock.return_value = None

        # Call the real method on the mock instance
        result = RedisBackedVODConnection.decrement_active_streams_and_check(conn)

        self.assertEqual(result, (True, False))
        self.assertEqual(state.active_streams, 0)

    def test_returns_success_and_remaining_when_other_streams_active(self):
        """When active_streams goes 2->1, should return (True, True)."""
        from apps.proxy.vod_proxy.multi_worker_connection_manager import RedisBackedVODConnection
        conn = MagicMock(spec=RedisBackedVODConnection)
        conn.session_id = 'test'

        state = MagicMock()
        state.active_streams = 2

        conn._acquire_lock.return_value = True
        conn._get_connection_state.return_value = state
        conn._save_connection_state.return_value = True
        conn._release_lock.return_value = None

        result = RedisBackedVODConnection.decrement_active_streams_and_check(conn)

        self.assertEqual(result, (True, True))
        self.assertEqual(state.active_streams, 1)

    def test_returns_failure_and_assumes_remaining_on_lock_contention(self):
        """Lock contention must return (False, True) — assume streams remain to be safe."""
        from apps.proxy.vod_proxy.multi_worker_connection_manager import RedisBackedVODConnection
        conn = MagicMock(spec=RedisBackedVODConnection)
        conn.session_id = 'test'
        conn._acquire_lock.return_value = False

        result = RedisBackedVODConnection.decrement_active_streams_and_check(conn)

        self.assertEqual(result, (False, True))
        conn._get_connection_state.assert_not_called()

    def test_returns_failure_when_already_at_zero(self):
        """When active_streams is already 0, should return (False, False)."""
        from apps.proxy.vod_proxy.multi_worker_connection_manager import RedisBackedVODConnection
        conn = MagicMock(spec=RedisBackedVODConnection)
        conn.session_id = 'test'

        state = MagicMock()
        state.active_streams = 0

        conn._acquire_lock.return_value = True
        conn._get_connection_state.return_value = state
        conn._release_lock.return_value = None

        result = RedisBackedVODConnection.decrement_active_streams_and_check(conn)

        self.assertEqual(result, (False, False))
        conn._save_connection_state.assert_not_called()

    def test_lock_always_released_even_on_exception(self):
        """Lock must be released even if an exception occurs inside."""
        from apps.proxy.vod_proxy.multi_worker_connection_manager import RedisBackedVODConnection
        conn = MagicMock(spec=RedisBackedVODConnection)
        conn.session_id = 'test'
        conn._acquire_lock.return_value = True
        conn._get_connection_state.side_effect = RuntimeError("Redis exploded")

        with self.assertRaises(RuntimeError):
            RedisBackedVODConnection.decrement_active_streams_and_check(conn)

        conn._release_lock.assert_called_once()


class TestIdleSessionReuseControls(TestCase):
    def test_find_matching_idle_session_short_circuits_when_reuse_disabled(self):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import (
            MultiWorkerVODConnectionManager,
        )

        mgr = MultiWorkerVODConnectionManager.__new__(MultiWorkerVODConnectionManager)
        mgr.redis_client = MagicMock()
        mgr.worker_id = "test-worker"
        mgr.IDLE_SESSION_REUSE_ENABLED = False

        result = mgr.find_matching_idle_session(
            content_type="movie",
            content_uuid="abc",
            client_ip="10.0.0.1",
            client_user_agent="UA",
        )

        self.assertIsNone(result)
        mgr.redis_client.scan.assert_not_called()


class TestFailedReuseCleanup(TestCase):
    def _make_state(self, session_id="reuse-session", active_streams=1, worker_id="test-worker"):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import SerializableConnectionState

        state = SerializableConnectionState(
            session_id=session_id,
            stream_url="http://example.com/movie.mkv",
            headers={"User-Agent": "UA"},
            m3u_profile_id=7,
            content_obj_type="movie",
            content_uuid="uuid-1",
            content_name="Movie 1",
            client_ip="10.0.0.1",
            client_user_agent="UA",
            worker_id=worker_id,
            user_id="1",
        )
        state.active_streams = active_streams
        return state

    def test_force_cleanup_after_failed_reuse_deletes_connection_state(self):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import RedisBackedVODConnection

        redis = FakeRedis()
        conn = RedisBackedVODConnection("reuse-session", redis_client=redis)
        conn._save_connection_state(self._make_state())

        result = conn.force_cleanup_after_failed_reuse(current_worker_id="test-worker")

        self.assertTrue(result)
        self.assertEqual(redis.hgetall(conn.connection_key), {})
        self.assertFalse(redis.exists(conn.lock_key))
