"""
Tests for VOD proxy profile connection counter fixes.

Covers:
  1. Atomic active_streams DECR+check via Redis Lua (no session-lock gating)
  2. Non-atomic GET-then-DECR in _decrement_profile_connections() (counter could go negative)
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


class TestDecrementActiveStreamsAndCheck(TestCase):
    """Atomic DECR+check via Redis Lua (no session lock)."""

    def test_returns_success_and_no_remaining_when_last_stream(self):
        from apps.proxy.vod_proxy.tests.test_vod_lock_contention import (
            LockAwareFakeRedis,
            _seed_session,
            _import_vod,
            _clear_script_cache,
        )

        RedisBackedVODConnection, _ = _import_vod()
        redis = LockAwareFakeRedis()
        _seed_session(redis, "prof-last", active_streams=1)
        conn = RedisBackedVODConnection("prof-last", redis)

        result = conn.decrement_active_streams_and_check()

        self.assertEqual(result, (True, False))
        self.assertEqual(conn.get_active_streams_count(), 0)

    def test_returns_success_and_remaining_when_other_streams_active(self):
        from apps.proxy.vod_proxy.tests.test_vod_lock_contention import (
            LockAwareFakeRedis,
            _seed_session,
            _import_vod,
        )

        RedisBackedVODConnection, _ = _import_vod()
        redis = LockAwareFakeRedis()
        _seed_session(redis, "prof-rem", active_streams=2)
        conn = RedisBackedVODConnection("prof-rem", redis)

        result = conn.decrement_active_streams_and_check()

        self.assertEqual(result, (True, True))
        self.assertEqual(conn.get_active_streams_count(), 1)

    def test_returns_failure_when_already_at_zero(self):
        from apps.proxy.vod_proxy.tests.test_vod_lock_contention import (
            LockAwareFakeRedis,
            _seed_session,
            _import_vod,
        )

        RedisBackedVODConnection, _ = _import_vod()
        redis = LockAwareFakeRedis()
        _seed_session(redis, "prof-zero", active_streams=0)
        conn = RedisBackedVODConnection("prof-zero", redis)

        result = conn.decrement_active_streams_and_check()

        self.assertEqual(result, (False, False))
        self.assertEqual(conn.get_active_streams_count(), 0)

    def test_returns_failure_when_no_session(self):
        from apps.proxy.vod_proxy.tests.test_vod_lock_contention import (
            LockAwareFakeRedis,
            _import_vod,
            _clear_script_cache,
        )

        RedisBackedVODConnection, _ = _import_vod()
        _clear_script_cache()
        redis = LockAwareFakeRedis()
        conn = RedisBackedVODConnection("missing", redis)

        result = conn.decrement_active_streams_and_check()

        self.assertEqual(result, (False, False))


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
