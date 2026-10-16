from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase

from apps.channels.models import Stream
from apps.m3u.models import M3UAccount
from apps.m3u.vpn_guard import (
    BLOCKED,
    ERROR,
    OK,
    GluetunClient,
    GuardConfig,
    ProbeResult,
    VpnGuard,
    _read_stream,
    probe_account,
)


class FakeRedis:
    def __init__(self):
        self.keys = {}

    def set(self, key, value, ex=None):
        self.keys[key] = (value, ex)

    def exists(self, key):
        return int(key in self.keys)


class FakeGluetun:
    def __init__(self, current_ip, next_ips):
        self.current_ip = current_ip
        self.next_ips = list(next_ips)
        self.rotations = 0

    def public_ip(self):
        return self.current_ip

    def rotate(self, previous_ip, timeout_seconds=90):
        self.rotations += 1
        self.current_ip = self.next_ips.pop(0)
        return self.current_ip


def results_by_ip(table):
    """Build a probe that answers per current exit IP."""

    def factory(gluetun):
        def probe():
            return [ProbeResult(name, outcome) for name, outcome in table[gluetun.current_ip]]

        return probe

    return factory


class GuardConfigTests(SimpleTestCase):
    def test_defaults(self):
        config = GuardConfig.from_env({})
        self.assertEqual(config.gluetun_url, "http://127.0.0.1:8000")
        self.assertEqual(config.block_statuses, frozenset({456}))
        self.assertEqual(config.gluetun_api_key, "")

    def test_reads_api_key_from_gluetun_role_json(self):
        config = GuardConfig.from_env(
            {"HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE": '{"auth":"apikey","apikey":"abc123"}'}
        )
        self.assertEqual(config.gluetun_api_key, "abc123")

    def test_explicit_api_key_wins(self):
        config = GuardConfig.from_env(
            {
                "VPN_GUARD_GLUETUN_API_KEY": "direct",
                "HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE": '{"apikey":"from-role"}',
            }
        )
        self.assertEqual(config.gluetun_api_key, "direct")

    def test_invalid_role_json_gives_empty_key(self):
        config = GuardConfig.from_env({"HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE": "not json"})
        self.assertEqual(config.gluetun_api_key, "")

    def test_overrides(self):
        config = GuardConfig.from_env(
            {
                "VPN_GUARD_BLOCK_STATUSES": "456, 403",
                "VPN_GUARD_MAX_ROTATIONS": "3",
                "VPN_GUARD_GLUETUN_URL": "http://gluetun:8000/",
            }
        )
        self.assertEqual(config.block_statuses, frozenset({456, 403}))
        self.assertEqual(config.max_rotations, 3)
        self.assertEqual(config.gluetun_url, "http://gluetun:8000")


class VpnGuardCycleTests(SimpleTestCase):
    def make_guard(self, gluetun, table, connections=0, **config):
        guard_config = GuardConfig(interval_seconds=1800, backoff_seconds=10800, **config)
        redis = FakeRedis()
        guard = VpnGuard(
            guard_config,
            gluetun,
            redis_client=redis,
            probe=results_by_ip(table)(gluetun),
            connection_count=lambda: connections,
        )
        return guard, redis

    def test_no_rotation_when_every_provider_works(self):
        gluetun = FakeGluetun("1.1.1.1", [])
        guard, _ = self.make_guard(gluetun, {"1.1.1.1": [("tiger", OK), ("diablo", OK)]})

        self.assertEqual(guard.run_cycle(), 1800)
        self.assertEqual(gluetun.rotations, 0)

    def test_skips_probe_and_rotation_while_something_plays(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2"])
        probe = MagicMock()
        guard = VpnGuard(GuardConfig(), gluetun, FakeRedis(), probe=probe, connection_count=lambda: 1)

        guard.run_cycle()

        probe.assert_not_called()
        self.assertEqual(gluetun.rotations, 0)

    def test_rotates_until_every_provider_works(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2", "3.3.3.3"])
        guard, redis = self.make_guard(
            gluetun,
            {
                "1.1.1.1": [("tiger", BLOCKED), ("diablo", OK)],
                "2.2.2.2": [("tiger", OK), ("diablo", BLOCKED)],
                "3.3.3.3": [("tiger", OK), ("diablo", OK)],
            },
        )

        self.assertEqual(guard.run_cycle(), 1800)
        self.assertEqual(gluetun.rotations, 2)
        self.assertEqual(gluetun.current_ip, "3.3.3.3")
        self.assertIn("vpn_guard:bad_ip:1.1.1.1", redis.keys)
        self.assertIn("vpn_guard:bad_ip:2.2.2.2", redis.keys)
        self.assertNotIn("vpn_guard:bad_ip:3.3.3.3", redis.keys)

    def test_skips_recently_blocked_ip_without_probing(self):
        gluetun = FakeGluetun("1.1.1.1", ["9.9.9.9", "3.3.3.3"])
        guard, redis = self.make_guard(
            gluetun,
            {
                "1.1.1.1": [("tiger", BLOCKED)],
                "3.3.3.3": [("tiger", OK)],
            },
        )
        redis.set("vpn_guard:bad_ip:9.9.9.9", "1")

        self.assertEqual(guard.run_cycle(), 1800)
        self.assertEqual(gluetun.current_ip, "3.3.3.3")

    def test_errors_other_than_blocks_do_not_rotate(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2"])
        guard, _ = self.make_guard(gluetun, {"1.1.1.1": [("tiger", ERROR)]})

        guard.run_cycle()

        self.assertEqual(gluetun.rotations, 0)

    def test_backs_off_after_max_rotations(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2", "3.3.3.3"])
        guard, _ = self.make_guard(
            gluetun,
            {ip: [("tiger", BLOCKED)] for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3")},
            max_rotations=2,
        )

        self.assertEqual(guard.run_cycle(), 10800)
        self.assertEqual(gluetun.rotations, 2)

    def test_dry_run_never_rotates(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2"])
        guard, redis = self.make_guard(gluetun, {"1.1.1.1": [("tiger", BLOCKED)]})

        guard.run_cycle(rotate=False)

        self.assertEqual(gluetun.rotations, 0)
        self.assertEqual(redis.keys, {})

    def test_stops_rotating_when_a_client_connects(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2", "3.3.3.3"])
        counts = iter([0, 0, 1])
        guard = VpnGuard(
            GuardConfig(),
            gluetun,
            FakeRedis(),
            probe=results_by_ip(
                {"1.1.1.1": [("tiger", BLOCKED)], "2.2.2.2": [("tiger", BLOCKED)]}
            )(gluetun),
            connection_count=lambda: next(counts),
        )

        guard.run_cycle()

        self.assertEqual(gluetun.rotations, 1)


class GluetunClientTests(SimpleTestCase):
    def make_client(self, ips):
        clock = iter(range(0, 1000, 3))
        client = GluetunClient("http://gluetun:8000", "key", sleep=lambda s: None, clock=lambda: next(clock))
        client.session = MagicMock()
        client.public_ip = MagicMock(side_effect=ips)
        return client

    def test_rotate_stops_then_starts_vpn_and_waits_for_new_ip(self):
        client = self.make_client(["1.1.1.1", "", "2.2.2.2"])

        self.assertEqual(client.rotate("1.1.1.1", timeout_seconds=60), "2.2.2.2")
        statuses = [c.kwargs["json"]["status"] for c in client.session.put.call_args_list]
        self.assertEqual(statuses, ["stopped", "running"])

    def test_rotate_accepts_same_ip_at_deadline(self):
        client = self.make_client(["1.1.1.1"] * 50)

        self.assertEqual(client.rotate("1.1.1.1", timeout_seconds=9), "1.1.1.1")

    def test_sends_api_key_header(self):
        client = GluetunClient("http://gluetun:8000", "secret-key")
        self.assertEqual(client.session.headers["X-API-Key"], "secret-key")


def fake_response(status, chunks=()):
    response = MagicMock()
    response.status_code = status
    response.iter_content.return_value = iter(chunks)
    return response


class ReadStreamTests(SimpleTestCase):
    @patch("apps.m3u.vpn_guard.requests.get")
    def test_block_status(self, get):
        get.return_value = fake_response(456)
        self.assertEqual(_read_stream("http://x", {}, {456})[0], BLOCKED)
        get.return_value.close.assert_called_once()

    @patch("apps.m3u.vpn_guard.requests.get")
    def test_other_status_is_error(self, get):
        get.return_value = fake_response(404)
        self.assertEqual(_read_stream("http://x", {}, {456})[0], ERROR)

    @patch("apps.m3u.vpn_guard.requests.get")
    def test_data_is_ok(self, get):
        get.return_value = fake_response(200, [b"x" * 8192] * 10)
        self.assertEqual(_read_stream("http://x", {}, {456})[0], OK)

    @patch("apps.m3u.vpn_guard.requests.get")
    def test_empty_body_is_error(self, get):
        get.return_value = fake_response(200, [])
        self.assertEqual(_read_stream("http://x", {}, {456})[0], ERROR)

    @patch("apps.m3u.vpn_guard.requests.get", side_effect=requests.ConnectionError())
    def test_network_failure_is_error(self, get):
        self.assertEqual(_read_stream("http://x", {}, {456})[0], ERROR)


class ProbeAccountTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Portal",
            account_type=M3UAccount.Types.STADNARD,
            server_url="http://provider.example.com/list.m3u",
        )

    def test_account_without_streams_is_error(self):
        self.assertEqual(probe_account(self.account, {456}).outcome, ERROR)

    @patch("apps.m3u.vpn_guard._read_stream")
    @patch("apps.m3u.vpn_guard._resolve_probe_request", return_value=("http://x", {}))
    def test_one_readable_stream_is_enough(self, _resolve, read):
        for i in range(2):
            Stream.objects.create(name=f"s{i}", url=f"http://x/{i}", m3u_account=self.account)
        read.side_effect = [(BLOCKED, "HTTP 456"), (OK, "HTTP 200")]

        self.assertEqual(probe_account(self.account, {456}).outcome, OK)

    @patch("apps.m3u.vpn_guard._read_stream", return_value=(BLOCKED, "HTTP 456"))
    @patch("apps.m3u.vpn_guard._resolve_probe_request", return_value=("http://x", {}))
    def test_blocked_when_no_stream_plays(self, _resolve, _read):
        Stream.objects.create(name="s", url="http://x/1", m3u_account=self.account)

        self.assertEqual(probe_account(self.account, {456}).outcome, BLOCKED)


class InitializationSkipTests(SimpleTestCase):
    @patch("dispatcharr.app_initialization._uwsgi_worker_id", return_value=None)
    @patch("sys.argv", ["/app/manage.py", "vpn_guard"])
    def test_vpn_guard_skips_startup_initialization(self, _worker_id):
        from dispatcharr.app_initialization import should_skip_initialization

        self.assertTrue(should_skip_initialization())
