import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.channels.models import Channel, Stream
from apps.accounts.models import User
from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.proxy.ts_proxy.constants import ChannelMetadataField
from apps.proxy.ts_proxy.redis_keys import RedisKeys
from apps.proxy.ts_proxy.server import ProxyServer
from apps.proxy.ts_proxy.services.channel_service import ChannelService
from apps.proxy.ts_proxy.stream_manager import StreamManager
from apps.proxy.ts_proxy.url_utils import get_stream_info_for_switch
from apps.proxy.ts_proxy.views import change_stream
from core.models import PROXY_PROFILE_NAME, StreamProfile, UserAgent


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def set(self, key, value):
        self.operations.append(("set", key, value))
        return self

    def delete(self, *keys):
        self.operations.append(("delete", keys))
        return self

    def hset(self, key, mapping=None, **kwargs):
        self.operations.append(("hset", key, mapping or kwargs.get("mapping", {})))
        return self

    def decr(self, key):
        self.operations.append(("decr", key))
        return self

    def incr(self, key):
        self.operations.append(("incr", key))
        return self

    def execute(self):
        for operation in self.operations:
            name = operation[0]
            if name == "set":
                _, key, value = operation
                self.redis.set(key, value)
            elif name == "delete":
                _, keys = operation
                self.redis.delete(*keys)
            elif name == "hset":
                _, key, mapping = operation
                self.redis.hset(key, mapping=mapping)
            elif name == "decr":
                _, key = operation
                self.redis.decr(key)
            elif name == "incr":
                _, key = operation
                self.redis.incr(key)
        self.operations.clear()
        return True


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        if isinstance(value, int):
            value = str(value).encode("utf-8")
        elif isinstance(value, str):
            value = value.encode("utf-8")
        self.values[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.hashes.pop(key, None)
        return True

    def hset(self, key, mapping=None, **kwargs):
        bucket = self.hashes.setdefault(key, {})
        updates = mapping or kwargs.get("mapping", {})
        for field, value in updates.items():
            if isinstance(field, str):
                field = field.encode("utf-8")
            if isinstance(value, int):
                value = str(value).encode("utf-8")
            elif isinstance(value, str):
                value = value.encode("utf-8")
            bucket[field] = value
        return True

    def hget(self, key, field):
        if isinstance(field, str):
            field = field.encode("utf-8")
        return self.hashes.get(key, {}).get(field)

    def incr(self, key):
        current = int(self.get(key) or 0)
        new_value = current + 1
        self.set(key, new_value)
        return new_value

    def decr(self, key):
        current = int(self.get(key) or 0)
        new_value = current - 1
        self.set(key, new_value)
        return new_value

    def pipeline(self):
        return _FakePipeline(self)


class TsProxyStalkerReconnectTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="secret",
        )
        self.user_agent = UserAgent.objects.create(
            name="Portal UA",
            user_agent="DispatcharrTest/1.0",
        )
        self.proxy_profile = StreamProfile.objects.create(
            name=PROXY_PROFILE_NAME,
            locked=True,
        )
        self.account = M3UAccount.objects.create(
            name="Stalker Runtime",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            user_agent=self.user_agent,
            custom_properties={
                "mac": "00:1A:79:00:00:40",
                "token": "OLD-TOKEN",
            },
        )
        self.account_profile = M3UAccountProfile.objects.create(
            m3u_account=self.account,
            name="Default",
            is_default=True,
            is_active=True,
            max_streams=0,
            search_pattern=r"world-news",
            replace_pattern="world-news-hd",
        )
        self.stream = Stream.objects.create(
            name="World News",
            url="http://portal.example.com/stalker_portal/server/load.php",
            m3u_account=self.account,
            stream_profile=self.proxy_profile,
            stream_hash="runtime-refresh-stream-hash",
            custom_properties={
                "portal_url": "http://portal.example.com/stalker_portal/server/load.php",
                "cmd": "ffmpeg http://upstream.example.com/live/world-news",
                "provider_type": "stalker",
            },
        )
        self.switch_context = {
            "url": "http://resolved.example.com/live/world-news-hd",
            "user_agent": "DispatcharrTest/2.0",
            "input_headers": {
                "Authorization": "Bearer TOKEN-NEW",
                "User-Agent": "DispatcharrTest/2.0",
            },
            "transcode": True,
            "stream_profile": self.proxy_profile.id,
            "stream_id": self.stream.id,
            "m3u_profile_id": self.account_profile.id,
        }

    def test_get_stream_info_for_switch_supports_preview_stream_hashes(self):
        fake_redis = _FakeRedis()

        with patch(
            "apps.channels.models.RedisClient.get_client",
            return_value=fake_redis,
        ), patch(
            "core.utils.RedisClient.get_client",
            return_value=fake_redis,
        ), patch.object(
            Stream,
            "get_stream",
            return_value=(self.stream.id, self.account_profile.id, None),
        ), patch.object(
            Stream,
            "get_stream_profile",
            return_value=self.proxy_profile,
        ), patch(
            "apps.proxy.ts_proxy.url_utils._resolve_live_stream_context",
            return_value={
                "url": "http://resolved.example.com/live/world-news",
                "user_agent": "DispatcharrTest/1.0",
                "input_headers": None,
            },
        ):
            stream_info = get_stream_info_for_switch(
                self.stream.stream_hash,
                self.stream.id,
            )

        self.assertEqual(
            stream_info["url"],
            "http://resolved.example.com/live/world-news-hd",
        )
        self.assertEqual(stream_info["stream_id"], self.stream.id)
        self.assertEqual(stream_info["m3u_profile_id"], self.account_profile.id)
        self.assertEqual(stream_info["user_agent"], "DispatcharrTest/1.0")
        self.assertFalse(stream_info["transcode"])

    def test_refresh_runtime_stream_url_updates_stalker_retry_url(self):
        manager = StreamManager.__new__(StreamManager)
        manager.channel_id = self.stream.stream_hash
        manager.current_stream_id = self.stream.id
        manager.url = "http://expired.example.com/live/world-news"
        manager.user_agent = "DispatcharrTest/1.0"
        manager.transcode = False
        manager.buffer = MagicMock()
        manager.buffer.redis_client = MagicMock()

        with patch(
            "apps.proxy.ts_proxy.stream_manager.get_stream_info_for_switch",
            return_value={
                "url": "http://resolved.example.com/live/world-news-hd",
                "user_agent": "DispatcharrTest/2.0",
                "transcode": True,
                "stream_profile": self.proxy_profile.id,
                "stream_id": self.stream.id,
                "m3u_profile_id": self.account_profile.id,
            },
        ):
            refreshed = manager._refresh_runtime_stream_url(reason="retry")

        self.assertTrue(refreshed)
        self.assertEqual(
            manager.url,
            "http://resolved.example.com/live/world-news-hd",
        )
        self.assertEqual(manager.user_agent, "DispatcharrTest/2.0")
        self.assertTrue(manager.transcode)

        metadata_mapping = manager.buffer.redis_client.hset.call_args.kwargs["mapping"]
        self.assertEqual(
            metadata_mapping[ChannelMetadataField.URL],
            "http://resolved.example.com/live/world-news-hd",
        )
        self.assertEqual(
            metadata_mapping[ChannelMetadataField.STREAM_ID],
            str(self.stream.id),
        )

    def test_change_stream_view_passes_runtime_switch_context(self):
        request = self.factory.post(
            f"/proxy/ts/change_stream/{self.stream.stream_hash}",
            {"stream_id": self.stream.id},
            format="json",
        )
        force_authenticate(request, user=self.admin)

        proxy_server = MagicMock()
        proxy_server.worker_id = "worker-1"
        proxy_server.stream_managers = {}

        with patch(
            "apps.proxy.ts_proxy.views.ProxyServer.get_instance",
            return_value=proxy_server,
        ), patch(
            "apps.proxy.ts_proxy.views.get_stream_info_for_switch",
            return_value=self.switch_context,
        ), patch(
            "apps.proxy.ts_proxy.views.ChannelService.change_stream_url",
            return_value={"status": "success", "direct_update": False},
        ) as mock_change_stream:
            response = change_stream(request, self.stream.stream_hash)

        self.assertEqual(response.status_code, 200)
        mock_change_stream.assert_called_once_with(
            self.stream.stream_hash,
            self.switch_context["url"],
            self.switch_context["user_agent"],
            self.stream.id,
            self.account_profile.id,
            self.switch_context["input_headers"],
        )

    def test_change_stream_url_backfills_missing_runtime_context_for_target_stream(self):
        proxy_server = MagicMock()
        proxy_server.redis_client = MagicMock()
        proxy_server.redis_client.keys.return_value = []
        proxy_server.redis_client.type.return_value = b"hash"
        proxy_server.stream_managers = {"channel-1": MagicMock(update_url=MagicMock(return_value=True))}
        proxy_server.stream_buffers = {}
        proxy_server.worker_id = "worker-1"
        proxy_server.check_if_channel_exists.return_value = True
        proxy_server.am_i_owner.return_value = True

        with patch(
            "apps.proxy.ts_proxy.services.channel_service.ProxyServer.get_instance",
            return_value=proxy_server,
        ), patch(
            "apps.proxy.ts_proxy.services.channel_service.get_stream_info_for_switch",
            return_value=self.switch_context,
        ) as mock_get_stream_info:
            result = ChannelService.change_stream_url(
                "channel-1",
                new_url=self.switch_context["url"],
                target_stream_id=self.stream.id,
            )

        self.assertEqual(result["status"], "success")
        mock_get_stream_info.assert_called_once_with("channel-1", self.stream.id)
        proxy_server.stream_managers["channel-1"].update_url.assert_called_once_with(
            self.switch_context["url"],
            self.stream.id,
            self.account_profile.id,
            self.switch_context["input_headers"],
        )

    def test_publish_stream_switch_event_includes_runtime_context(self):
        proxy_server = MagicMock()
        proxy_server.worker_id = "worker-1"
        proxy_server.redis_client = MagicMock()

        with patch(
            "apps.proxy.ts_proxy.services.channel_service.ProxyServer.get_instance",
            return_value=proxy_server,
        ):
            ChannelService._publish_stream_switch_event(
                "channel-1",
                self.switch_context["url"],
                self.switch_context["user_agent"],
                self.stream.id,
                self.account_profile.id,
                self.switch_context["input_headers"],
            )

        publish_args = proxy_server.redis_client.publish.call_args.args
        self.assertEqual(publish_args[0], RedisKeys.events_channel("channel-1"))
        payload = json.loads(publish_args[1])
        self.assertEqual(payload["stream_id"], self.stream.id)
        self.assertEqual(payload["m3u_profile_id"], self.account_profile.id)
        self.assertEqual(payload["input_headers"], self.switch_context["input_headers"])

    def test_handle_stream_switch_event_preserves_runtime_context_on_owner(self):
        server = ProxyServer.__new__(ProxyServer)
        server.redis_client = MagicMock()
        server.stream_managers = {
            "channel-1": MagicMock(update_url=MagicMock(return_value=True))
        }
        server._publish_stream_switch_result = MagicMock(return_value=True)

        success = server._handle_stream_switch_event(
            "channel-1",
            {
                "url": self.switch_context["url"],
                "user_agent": self.switch_context["user_agent"],
                "stream_id": self.stream.id,
                "m3u_profile_id": self.account_profile.id,
                "input_headers": self.switch_context["input_headers"],
            },
        )

        self.assertTrue(success)
        server.stream_managers["channel-1"].update_url.assert_called_once_with(
            self.switch_context["url"],
            self.stream.id,
            self.account_profile.id,
            self.switch_context["input_headers"],
        )

        metadata_mapping = server.redis_client.hset.call_args.kwargs["mapping"]
        self.assertEqual(
            metadata_mapping[ChannelMetadataField.URL],
            self.switch_context["url"],
        )
        self.assertEqual(
            metadata_mapping[ChannelMetadataField.USER_AGENT],
            self.switch_context["user_agent"],
        )
        self.assertEqual(
            json.loads(metadata_mapping[ChannelMetadataField.INPUT_HEADERS]),
            self.switch_context["input_headers"],
        )
        self.assertEqual(
            metadata_mapping[ChannelMetadataField.STREAM_ID],
            str(self.stream.id),
        )
        self.assertEqual(
            metadata_mapping[ChannelMetadataField.M3U_PROFILE],
            str(self.account_profile.id),
        )

    def test_buffering_timeout_reconnects_current_stream_before_failover(self):
        manager = StreamManager.__new__(StreamManager)
        manager.channel_id = "channel-1"
        manager.current_stream_id = self.stream.id
        manager.buffering_speed = 1.0
        manager.buffering_timeout = 15
        manager.buffering = True
        manager.buffering_start_time = 100.0
        manager.buffering_recovery_attempts = 0
        manager.max_buffering_recovery_attempts = 1
        manager.buffering_recovery_in_progress = False
        manager.url_switching = False
        manager.connected = True
        manager.buffer = MagicMock()
        manager.buffer.redis_client = MagicMock()
        manager._refresh_runtime_stream_url = MagicMock(return_value=True)
        manager._close_socket = MagicMock()
        manager._try_next_stream = MagicMock(return_value=True)

        with patch("apps.proxy.ts_proxy.stream_manager.time.time", return_value=116.0):
            manager._parse_ffmpeg_stats("frame= 120 fps=30 speed=0.99x")

        manager._refresh_runtime_stream_url.assert_called_once_with(
            reason="buffering_timeout"
        )
        manager._close_socket.assert_called_once()
        manager._try_next_stream.assert_not_called()
        self.assertEqual(manager.buffering_recovery_attempts, 1)
        self.assertTrue(manager.buffering_recovery_in_progress)
        self.assertFalse(manager.buffering)
        self.assertIsNone(manager.buffering_start_time)

    def test_buffering_timeout_fails_over_after_current_stream_recovery_budget_is_used(self):
        manager = StreamManager.__new__(StreamManager)
        manager.channel_id = "channel-1"
        manager.current_stream_id = self.stream.id
        manager.buffering_speed = 1.0
        manager.buffering_timeout = 15
        manager.buffering = True
        manager.buffering_start_time = 100.0
        manager.buffering_recovery_attempts = 1
        manager.max_buffering_recovery_attempts = 1
        manager.buffering_recovery_in_progress = False
        manager.url_switching = False
        manager.connected = True
        manager.buffer = MagicMock()
        manager.buffer.redis_client = MagicMock()
        manager._refresh_runtime_stream_url = MagicMock(return_value=True)
        manager._close_socket = MagicMock()
        manager._try_next_stream = MagicMock(return_value=False)

        with patch("apps.proxy.ts_proxy.stream_manager.time.time", return_value=116.0):
            manager._parse_ffmpeg_stats("frame= 120 fps=30 speed=0.99x")

        manager._refresh_runtime_stream_url.assert_not_called()
        manager._close_socket.assert_not_called()
        manager._try_next_stream.assert_called_once()

    def test_same_stream_recovery_prepares_stalker_retry_before_failover(self):
        manager = StreamManager.__new__(StreamManager)
        manager.channel_id = self.stream.stream_hash
        manager.current_stream_id = self.stream.id
        manager.url = "http://expired.example.com/live/world-news"
        manager.user_agent = "DispatcharrTest/1.0"
        manager.input_headers = {}
        manager.transcode = False
        manager.retry_count = 3
        manager.same_stream_recovery_attempts = 0
        manager.max_same_stream_recovery_attempts = 1
        manager.buffering = True
        manager.buffering_start_time = 10.0
        manager.buffering_recovery_in_progress = True
        manager.needs_reconnect = True
        manager.needs_stream_switch = True
        manager.url_switching = False
        manager.connected = True
        manager.buffer = MagicMock()
        manager.buffer.redis_client = MagicMock()
        manager._close_socket = MagicMock()
        manager._wait_for_existing_processes_to_close = MagicMock(return_value=True)

        with patch(
            "apps.proxy.ts_proxy.stream_manager.time.time",
            return_value=250.0,
        ), patch(
            "apps.proxy.ts_proxy.stream_manager.get_stream_info_for_switch",
            return_value={
                "url": "http://resolved.example.com/live/world-news-hd",
                "user_agent": "DispatcharrTest/2.0",
                "input_headers": {"Authorization": "Bearer TOKEN-NEW"},
                "transcode": True,
                "stream_profile": self.proxy_profile.id,
                "stream_id": self.stream.id,
                "m3u_profile_id": self.account_profile.id,
            },
        ):
            recovered = manager._attempt_same_stream_recovery(
                reason="max_retries_exceeded"
            )

        self.assertTrue(recovered)
        self.assertEqual(manager.same_stream_recovery_attempts, 1)
        self.assertEqual(manager.retry_count, 0)
        self.assertEqual(
            manager.url,
            "http://resolved.example.com/live/world-news-hd",
        )
        self.assertEqual(manager.user_agent, "DispatcharrTest/2.0")
        self.assertEqual(
            manager.input_headers,
            {"Authorization": "Bearer TOKEN-NEW"},
        )
        self.assertTrue(manager.transcode)
        self.assertFalse(manager.connected)
        self.assertFalse(manager.buffering)
        self.assertIsNone(manager.buffering_start_time)
        self.assertFalse(manager.buffering_recovery_in_progress)
        self.assertFalse(manager.needs_reconnect)
        self.assertFalse(manager.needs_stream_switch)
        manager._close_socket.assert_called_once()
        manager._wait_for_existing_processes_to_close.assert_called_once()

    def test_run_uses_same_stream_recovery_before_health_failover_for_stalker(self):
        manager = StreamManager.__new__(StreamManager)
        manager.channel_id = self.stream.stream_hash
        manager.worker_id = "worker-1"
        manager.current_stream_id = self.stream.id
        manager.url = "http://expired.example.com/live/world-news"
        manager.user_agent = "DispatcharrTest/1.0"
        manager.input_headers = {}
        manager.transcode = False
        manager.running = True
        manager.connected = False
        manager.retry_count = 0
        manager.max_retries = 1
        manager.same_stream_recovery_attempts = 0
        manager.max_same_stream_recovery_attempts = 1
        manager.needs_reconnect = False
        manager.needs_stream_switch = True
        manager.stop_requested = False
        manager.url_switching = False
        manager.url_switch_start_time = 0
        manager.url_switch_timeout = 30
        manager.stream_type = None
        manager.transcode_process_active = False
        manager._buffer_check_timers = []
        manager.stopping = False
        manager.socket = None
        manager.transcode_process = None
        manager.current_response = None
        manager.current_session = None
        manager.tried_stream_ids = set()
        manager.buffer = MagicMock()
        manager.buffer.redis_client = MagicMock()
        manager.buffer.channel_id = self.stream.stream_hash
        manager._close_all_connections = MagicMock()
        manager._try_next_stream = MagicMock(return_value=True)

        def _same_stream_recovery(**kwargs):
            manager.running = False
            return True

        manager._attempt_same_stream_recovery = MagicMock(
            side_effect=_same_stream_recovery
        )

        with patch(
            "apps.proxy.ts_proxy.stream_manager.threading.Thread",
            return_value=MagicMock(start=MagicMock()),
        ), patch(
            "apps.proxy.ts_proxy.stream_manager.ConfigHelper.max_stream_switches",
            return_value=0,
        ), patch(
            "apps.proxy.ts_proxy.stream_manager.connection.close",
        ):
            manager.run()

        manager._attempt_same_stream_recovery.assert_called_once_with(
            reason="health_stream_switch"
        )
        manager._try_next_stream.assert_not_called()

    def test_switch_stream_assignment_moves_canonical_keys_to_new_stream(self):
        channel = Channel.objects.create(
            channel_number=101,
            name="World News",
            stream_profile=self.proxy_profile,
        )
        alternate_stream = Stream.objects.create(
            name="World News Backup",
            url="http://portal.example.com/stalker_portal/server/load.php",
            m3u_account=self.account,
            stream_profile=self.proxy_profile,
            stream_hash="runtime-refresh-stream-hash-backup",
            custom_properties={
                "portal_url": "http://portal.example.com/stalker_portal/server/load.php",
                "cmd": "ffmpeg http://upstream.example.com/live/world-news-backup",
                "provider_type": "stalker",
            },
        )
        old_profile = M3UAccountProfile.objects.create(
            m3u_account=self.account,
            name="Legacy",
            is_default=False,
            is_active=True,
            max_streams=2,
            search_pattern=r"world-news",
            replace_pattern="world-news",
        )
        new_profile = M3UAccountProfile.objects.create(
            m3u_account=self.account,
            name="Backup",
            is_default=False,
            is_active=True,
            max_streams=2,
            search_pattern=r"world-news",
            replace_pattern="world-news-backup",
        )

        fake_redis = _FakeRedis()
        fake_redis.set(f"channel_stream:{channel.id}", self.stream.id)
        fake_redis.set(f"stream_profile:{self.stream.id}", old_profile.id)
        fake_redis.set(f"profile_connections:{old_profile.id}", 1)

        with patch(
            "apps.channels.models.RedisClient.get_client",
            return_value=fake_redis,
        ):
            switched = channel.switch_stream_assignment(
                alternate_stream.id,
                new_profile.id,
            )

        self.assertTrue(switched)
        self.assertEqual(
            fake_redis.get(f"channel_stream:{channel.id}"),
            str(alternate_stream.id).encode("utf-8"),
        )
        self.assertIsNone(fake_redis.get(f"stream_profile:{self.stream.id}"))
        self.assertEqual(
            fake_redis.get(f"stream_profile:{alternate_stream.id}"),
            str(new_profile.id).encode("utf-8"),
        )
        self.assertEqual(
            fake_redis.get(f"profile_connections:{old_profile.id}"),
            b"0",
        )
        self.assertEqual(
            fake_redis.get(f"profile_connections:{new_profile.id}"),
            b"1",
        )

        metadata_key = RedisKeys.channel_metadata(str(channel.uuid))
        self.assertEqual(
            fake_redis.hget(metadata_key, ChannelMetadataField.STREAM_ID),
            str(alternate_stream.id).encode("utf-8"),
        )
        self.assertEqual(
            fake_redis.hget(metadata_key, ChannelMetadataField.M3U_PROFILE),
            str(new_profile.id).encode("utf-8"),
        )
