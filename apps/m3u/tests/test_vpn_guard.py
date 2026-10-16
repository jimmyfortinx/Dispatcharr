import sys
from fnmatch import fnmatchcase
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase

from apps.channels.models import Channel, ChannelStream, Stream
from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerError
from apps.m3u.vpn_guard import (
    BLOCKED,
    ERROR,
    OK,
    GluetunClient,
    GuardConfig,
    ProbeResult,
    VpnGuard,
    _probe_streams,
    _read_stream,
    active_connection_count,
    probe_account,
)


class FakeRedis:
    def __init__(self, keys=()):
        self.keys = {key: ("1", None) for key in keys}

    def set(self, key, value, ex=None):
        self.keys[key] = (value, ex)

    def exists(self, key):
        return int(key in self.keys)

    def scan_iter(self, match, count=None):
        return iter([k for k in self.keys if fnmatchcase(k, match)])


class FakeGluetun:
    def __init__(self, current_ip, next_ips):
        self.current_ip = current_ip
        self.next_ips = list(next_ips)
        self.rotations = 0
        self.ensure_calls = 0

    def public_ip(self):
        return self.current_ip

    def ensure_running(self):
        self.ensure_calls += 1

    def rotate(self, previous_ip, timeout_seconds=90):
        self.rotations += 1
        self.current_ip = self.next_ips.pop(0)
        return self.current_ip


def probe_by_ip(gluetun, table):
    """A probe that answers per current exit IP."""

    def probe():
        return [ProbeResult(name, outcome) for name, outcome in table[gluetun.current_ip]]

    return probe


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
    def make_guard(self, gluetun, table, connections=0, redis=None, **config):
        guard_config = GuardConfig(interval_seconds=1800, backoff_seconds=10800, **config)
        redis = redis if redis is not None else FakeRedis()
        guard = VpnGuard(
            guard_config,
            gluetun,
            redis_factory=lambda: redis,
            probe=probe_by_ip(gluetun, table),
            connection_count=lambda _redis: connections,
        )
        return guard, redis

    def test_no_rotation_when_every_provider_works(self):
        gluetun = FakeGluetun("1.1.1.1", [])
        guard, _ = self.make_guard(gluetun, {"1.1.1.1": [("tiger", OK), ("diablo", OK)]})

        self.assertEqual(guard.run_cycle(), 1800)
        self.assertEqual(gluetun.rotations, 0)

    def test_restores_a_stopped_vpn_before_probing(self):
        gluetun = FakeGluetun("1.1.1.1", [])
        guard, _ = self.make_guard(gluetun, {"1.1.1.1": [("tiger", OK)]})

        guard.run_cycle()

        self.assertEqual(gluetun.ensure_calls, 1)

    def test_skips_probe_and_rotation_while_something_plays(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2"])
        probe = MagicMock()
        guard = VpnGuard(
            GuardConfig(), gluetun, lambda: FakeRedis(), probe=probe, connection_count=lambda _r: 1
        )

        guard.run_cycle()

        probe.assert_not_called()
        self.assertEqual(gluetun.rotations, 0)

    def test_unknown_viewer_state_stops_the_cycle(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2"])
        guard = VpnGuard(GuardConfig(), gluetun, lambda: None, probe=MagicMock())

        with self.assertRaises(RuntimeError):
            guard.run_cycle()
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

    def test_an_error_after_rotation_is_not_success(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2", "3.3.3.3"])
        guard, redis = self.make_guard(
            gluetun,
            {
                "1.1.1.1": [("tiger", BLOCKED)],
                "2.2.2.2": [("tiger", ERROR)],
                "3.3.3.3": [("tiger", OK)],
            },
        )

        self.assertEqual(guard.run_cycle(), 1800)
        self.assertEqual(gluetun.current_ip, "3.3.3.3")
        # An error is not proof of a block: the IP is not remembered as bad.
        self.assertNotIn("vpn_guard:bad_ip:2.2.2.2", redis.keys)

    def test_accounts_failing_before_rotation_do_not_block_success(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2"])
        guard, _ = self.make_guard(
            gluetun,
            {
                "1.1.1.1": [("tiger", BLOCKED), ("empty", ERROR)],
                "2.2.2.2": [("tiger", OK), ("empty", ERROR)],
            },
        )

        self.assertEqual(guard.run_cycle(), 1800)
        self.assertEqual(gluetun.rotations, 1)

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

    def test_dry_run_never_touches_the_vpn(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2"])
        guard, redis = self.make_guard(gluetun, {"1.1.1.1": [("tiger", BLOCKED)]})

        guard.run_cycle(rotate=False)

        self.assertEqual(gluetun.rotations, 0)
        self.assertEqual(gluetun.ensure_calls, 0)
        self.assertEqual(redis.keys, {})

    def test_stops_rotating_when_a_client_connects(self):
        gluetun = FakeGluetun("1.1.1.1", ["2.2.2.2", "3.3.3.3"])
        counts = iter([0, 0, 1])
        guard = VpnGuard(
            GuardConfig(),
            gluetun,
            lambda: FakeRedis(),
            probe=probe_by_ip(
                gluetun, {"1.1.1.1": [("tiger", BLOCKED)], "2.2.2.2": [("tiger", BLOCKED)]}
            ),
            connection_count=lambda _r: next(counts),
        )

        guard.run_cycle()

        self.assertEqual(gluetun.rotations, 1)


class ActiveConnectionCountTests(SimpleTestCase):
    def test_counts_live_timeshift_and_vod_clients(self):
        redis = FakeRedis(
            [
                "live:channel:abc:clients:c1",
                "timeshift:channel:abc:clients:c2",
                "vod_persistent_connection:c3",
                "live:channel:abc:metadata",
            ]
        )
        self.assertEqual(active_connection_count(redis), 3)

    def test_redis_errors_propagate(self):
        redis = MagicMock()
        redis.scan_iter.side_effect = ConnectionError("down")
        with self.assertRaises(ConnectionError):
            active_connection_count(redis)

    def test_missing_redis_is_an_error(self):
        with self.assertRaises(RuntimeError):
            active_connection_count(None)


class GluetunClientTests(SimpleTestCase):
    def make_client(self, ips):
        clock = iter(range(0, 1000, 3))
        client = GluetunClient(
            "http://gluetun:8000", "key", sleep=lambda s: None, clock=lambda: next(clock)
        )
        client.session = MagicMock()
        client.public_ip = MagicMock(side_effect=ips)
        return client

    def put_statuses(self, client):
        return [c.kwargs["json"]["status"] for c in client.session.put.call_args_list]

    def test_rotate_stops_then_starts_vpn_and_waits_for_new_ip(self):
        client = self.make_client(["1.1.1.1", "", "2.2.2.2"])

        self.assertEqual(client.rotate("1.1.1.1", timeout_seconds=60), "2.2.2.2")
        self.assertEqual(self.put_statuses(client), ["stopped", "running"])

    def test_rotate_accepts_same_ip_at_deadline(self):
        client = self.make_client(["1.1.1.1"] * 50)

        self.assertEqual(client.rotate("1.1.1.1", timeout_seconds=9), "1.1.1.1")

    def test_failed_start_is_retried(self):
        client = self.make_client(["2.2.2.2"])
        ok = MagicMock()
        client.session.put.side_effect = [ok, requests.ConnectionError(), ok]
        client.vpn_status = MagicMock(return_value="stopped")

        self.assertEqual(client.rotate("1.1.1.1", timeout_seconds=60), "2.2.2.2")
        self.assertEqual(self.put_statuses(client), ["stopped", "running", "running"])

    def test_ensure_running_starts_a_stopped_vpn(self):
        client = self.make_client([])
        client.vpn_status = MagicMock(return_value="stopped")

        client.ensure_running()

        self.assertEqual(self.put_statuses(client), ["running"])

    def test_ensure_running_leaves_a_running_vpn(self):
        client = self.make_client([])
        client.vpn_status = MagicMock(return_value="running")

        client.ensure_running()

        client.session.put.assert_not_called()

    def test_sends_api_key_header(self):
        client = GluetunClient("http://gluetun:8000", "secret-key")
        self.assertEqual(client.session.headers["X-API-Key"], "secret-key")


def fake_response(status, chunks=(), content_type="video/mp2t", url="http://x/stream"):
    response = MagicMock()
    response.status_code = status
    response.headers = {"Content-Type": content_type}
    response.url = url
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

    @patch("apps.m3u.vpn_guard.requests.get")
    def test_html_page_is_error(self, get):
        get.return_value = fake_response(200, [b"<html>expired</html>"], content_type="text/html")
        self.assertEqual(_read_stream("http://x", {}, {456})[0], ERROR)

    @patch("apps.m3u.vpn_guard.requests.get", side_effect=requests.ConnectionError())
    def test_network_failure_is_error(self, get):
        self.assertEqual(_read_stream("http://x", {}, {456})[0], ERROR)

    @patch("apps.m3u.vpn_guard.requests.get")
    def test_hls_playlist_follows_to_a_blocked_segment(self, get):
        playlist = b"#EXTM3U\n#EXTINF:6,\nseg1.ts\n"
        get.side_effect = [
            fake_response(200, [playlist], "application/vnd.apple.mpegurl", "http://x/live/index.m3u8"),
            fake_response(456),
        ]

        self.assertEqual(_read_stream("http://x/live/index.m3u8", {}, {456})[0], BLOCKED)
        self.assertEqual(get.call_args_list[1].args[0], "http://x/live/seg1.ts")

    @patch("apps.m3u.vpn_guard.requests.get")
    def test_hls_master_variant_and_segment(self, get):
        master = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhd/index.m3u8\n"
        variant = b"#EXTM3U\n#EXTINF:6,\nseg1.ts\n"
        get.side_effect = [
            fake_response(200, [master], "application/x-mpegurl", "http://x/master.m3u8"),
            fake_response(200, [variant], "application/x-mpegurl", "http://x/hd/index.m3u8"),
            fake_response(200, [b"\x47" * 1000]),
        ]

        self.assertEqual(_read_stream("http://x/master.m3u8", {}, {456})[0], OK)

    @patch("apps.m3u.vpn_guard.requests.get")
    def test_slow_trickle_stops_at_the_deadline(self, get):
        get.return_value = fake_response(200, (b"x" for _ in range(10**6)))
        ticks = iter(range(0, 10**6))

        outcome, detail = _read_stream("http://x", {}, {456}, clock=lambda: next(ticks))

        self.assertEqual(outcome, OK)
        self.assertLess(int(detail.split(", ")[1].split()[0]), 100)


class ProbeAccountTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Portal",
            account_type=M3UAccount.Types.STADNARD,
            server_url="http://provider.example.com/list.m3u",
        )

    def test_account_without_streams_is_error(self):
        self.assertEqual(probe_account(self.account, {456}).outcome, ERROR)

    def test_account_without_active_profile_is_error(self):
        self.account.profiles.update(is_active=False)
        Stream.objects.create(name="s", url="http://x/1", m3u_account=self.account)

        result = probe_account(self.account, {456})

        self.assertEqual((result.outcome, result.detail), (ERROR, "no active profile"))

    @patch("apps.m3u.vpn_guard._read_stream", return_value=(OK, "HTTP 200"))
    @patch("apps.m3u.vpn_guard._resolve_probe_request", return_value=("http://x", {}))
    def test_uses_an_active_profile_when_the_default_is_inactive(self, resolve, _read):
        self.account.profiles.update(is_active=False)
        active = self.account.profiles.create(name="Second", is_active=True, max_streams=1)
        Stream.objects.create(name="s", url="http://x/1", m3u_account=self.account)

        self.assertEqual(probe_account(self.account, {456}).outcome, OK)
        self.assertEqual(resolve.call_args.args[1], active)

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

    def test_portal_block_during_resolution_counts_as_blocked(self):
        Stream.objects.create(name="s", url="http://x/1", m3u_account=self.account)
        try:
            try:
                raise requests.HTTPError(response=MagicMock(status_code=456))
            except requests.HTTPError as exc:
                raise StalkerError("Request failed: 456") from exc
        except StalkerError as exc:
            portal_error = exc

        with patch("apps.m3u.vpn_guard._resolve_probe_request", side_effect=portal_error):
            result = probe_account(self.account, {456})

        self.assertEqual(result.outcome, BLOCKED)
        self.assertIn("portal HTTP 456", result.detail)

    def test_stream_in_several_channels_is_sampled_once(self):
        stream = Stream.objects.create(name="s", url="http://x/1", m3u_account=self.account)
        other = Stream.objects.create(name="t", url="http://x/2", m3u_account=self.account)
        for number in (1, 2, 3):
            channel = Channel.objects.create(name=f"c{number}", channel_number=number)
            ChannelStream.objects.create(channel=channel, stream=stream)
        channel = Channel.objects.create(name="c4", channel_number=4)
        ChannelStream.objects.create(channel=channel, stream=other)

        picked = _probe_streams(self.account)

        self.assertEqual(sorted(s.id for s in picked), sorted([stream.id, other.id]))


class StalkerProbeRequestTests(TestCase):
    @patch("apps.proxy.live_proxy.url_utils._resolve_live_stream_context")
    def test_stalker_url_goes_through_the_profile_transform(self, context):
        from apps.m3u.vpn_guard import _resolve_probe_request

        account = M3UAccount.objects.create(
            name="Stalker",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com",
        )
        profile = account.profiles.get(is_default=True)
        profile.search_pattern = "portal.example.com"
        profile.replace_pattern = "mirror.example.com"
        profile.save()
        stream = Stream.objects.create(name="s", url="http://portal.example.com/p", m3u_account=account)
        context.return_value = {
            "url": "http://portal.example.com/play/live.php?stream=1",
            "user_agent": "MAG",
            "input_headers": {"Cookie": "mac=1"},
        }

        url, headers = _resolve_probe_request(stream, profile)

        self.assertEqual(url, "http://mirror.example.com/play/live.php?stream=1")
        self.assertEqual(headers, {"Cookie": "mac=1", "User-Agent": "MAG"})


class InitializationSkipTests(SimpleTestCase):
    @patch("dispatcharr.app_initialization._uwsgi_worker_id", return_value=None)
    @patch.object(sys, "argv", ["/app/manage.py", "vpn_guard"])
    def test_vpn_guard_skips_startup_initialization(self, _worker_id):
        from dispatcharr.app_initialization import should_skip_initialization

        self.assertTrue(should_skip_initialization())

    @patch.object(sys, "argv", ["/app/manage.py", "vpn_guard"])
    def test_vpn_guard_skips_plugin_discovery(self):
        from django.apps import apps as django_apps

        with patch("apps.plugins.loader.PluginManager.get") as get_manager:
            django_apps.get_app_config("plugins").ready()

        get_manager.assert_not_called()
