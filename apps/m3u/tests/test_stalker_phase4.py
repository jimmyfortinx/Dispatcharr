from unittest.mock import patch

from django.test import TestCase

from apps.channels.models import ChannelGroup, ChannelGroupM3UAccount, Stream
from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerChannelDiscoveryResult
from apps.m3u.tasks import _refresh_single_m3u_account_impl


class StalkerPhase4StreamImportTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Streams",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={"mac": "00:1A:79:00:00:40"},
        )
        self.news_group = ChannelGroup.objects.create(name="News")
        self.sports_group = ChannelGroup.objects.create(name="Sports")
        ChannelGroupM3UAccount.objects.create(
            channel_group=self.news_group,
            m3u_account=self.account,
            enabled=True,
            custom_properties={"stalker_genre_id": "10"},
        )
        ChannelGroupM3UAccount.objects.create(
            channel_group=self.sports_group,
            m3u_account=self.account,
            enabled=False,
            custom_properties={"stalker_genre_id": "11"},
        )

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.m3u.tasks.sync_auto_channels", return_value="")
    @patch("apps.m3u.tasks.cleanup_stale_group_relationships", return_value=0)
    @patch("apps.m3u.tasks.cleanup_streams", return_value=0)
    @patch("apps.m3u.tasks.refresh_m3u_groups")
    @patch("apps.m3u.tasks.StalkerClient.discover_live_channels")
    def test_full_refresh_imports_stalker_streams_idempotently(
        self,
        mock_discover_channels,
        mock_refresh_groups,
        _mock_cleanup_streams,
        _mock_cleanup_groups,
        _mock_sync,
        _mock_update,
    ):
        mock_refresh_groups.return_value = (
            [],
            {
                "News": {"stalker_genre_id": "10"},
                "Sports": {"stalker_genre_id": "11"},
            },
        )
        mock_discover_channels.return_value = StalkerChannelDiscoveryResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            genres=[
                {"id": "10", "title": "News"},
                {"id": "11", "title": "Sports"},
            ],
            channels=[
                {
                    "id": "5001",
                    "name": "World News",
                    "cmd": "ffmpeg http://upstream.example.com/live/world-news",
                    "cmd_id": "1001",
                    "cmd_ch_id": "7001",
                    "tv_genre_id": "10",
                    "genre_id": "10",
                    "genre_name": "News",
                    "logo": "world-news.png",
                    "logo_url": "http://portal.example.com/stalker_portal/misc/logos/320/world-news.png",
                    "xmltv_id": "world.news",
                },
                {
                    "id": "5002",
                    "name": "Sports Central",
                    "cmd": "ffmpeg http://upstream.example.com/live/sports-central",
                    "cmd_id": "1002",
                    "cmd_ch_id": "7002",
                    "tv_genre_id": "11",
                    "genre_id": "11",
                    "genre_name": "Sports",
                    "logo": "sports-central.png",
                    "logo_url": "http://portal.example.com/stalker_portal/misc/logos/320/sports-central.png",
                    "xmltv_id": "sports.central",
                },
            ],
            token="TOKEN-456",
            used_authentication=True,
        )

        result_first = _refresh_single_m3u_account_impl(self.account.id)
        result_second = _refresh_single_m3u_account_impl(self.account.id)

        self.assertIsNone(result_first)
        self.assertIsNone(result_second)

        streams = Stream.objects.filter(m3u_account=self.account)
        self.assertEqual(streams.count(), 1)

        stream = streams.get()
        self.assertEqual(stream.name, "World News")
        self.assertEqual(stream.channel_group, self.news_group)
        self.assertEqual(
            stream.url,
            "http://portal.example.com/stalker_portal/server/load.php",
        )
        self.assertEqual(
            stream.logo_url,
            "http://portal.example.com/stalker_portal/misc/logos/320/world-news.png",
        )
        self.assertEqual(stream.tvg_id, "world.news")
        self.assertEqual(stream.stream_id, 1001)
        self.assertEqual(stream.custom_properties["cmd"], "ffmpeg http://upstream.example.com/live/world-news")
        self.assertEqual(stream.custom_properties["cmd_id"], "1001")
        self.assertEqual(stream.custom_properties["cmd_ch_id"], "7001")
        self.assertEqual(stream.custom_properties["genre_id"], "10")
        self.assertEqual(stream.custom_properties["provider_type"], "stalker")

        self.account.refresh_from_db()
        self.assertEqual(self.account.status, M3UAccount.Status.SUCCESS)
        self.assertEqual(self.account.custom_properties["token"], "TOKEN-456")
        self.assertIn("Streams: 1 created, 0 updated", self.account.last_message)
