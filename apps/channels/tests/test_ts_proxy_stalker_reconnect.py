import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.channels.models import Stream
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


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        if isinstance(value, int):
            value = str(value).encode("utf-8")
        elif isinstance(value, str):
            value = value.encode("utf-8")
        self.values[key] = value
        return True


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
        ), patch(
            "apps.proxy.ts_proxy.url_utils.resolve_live_stream_url",
            return_value="http://resolved.example.com/live/world-news",
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
