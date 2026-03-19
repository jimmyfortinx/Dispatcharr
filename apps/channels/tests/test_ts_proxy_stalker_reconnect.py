from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.channels.models import Stream
from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.proxy.ts_proxy.constants import ChannelMetadataField
from apps.proxy.ts_proxy.stream_manager import StreamManager
from apps.proxy.ts_proxy.url_utils import get_stream_info_for_switch
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
