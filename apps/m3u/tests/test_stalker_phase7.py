from unittest.mock import patch

from django.test import TestCase

from apps.channels.models import Channel, ChannelGroup, ChannelGroupM3UAccount, ChannelStream
from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerChannelDiscoveryResult
from apps.m3u.tasks import _refresh_single_m3u_account_impl


class StalkerPhase7ChannelAutoSyncTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Auto Sync",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={"mac": "00:1A:79:00:00:40"},
        )
        self.news_group = ChannelGroup.objects.create(name="News")
        ChannelGroupM3UAccount.objects.create(
            channel_group=self.news_group,
            m3u_account=self.account,
            enabled=True,
            auto_channel_sync=True,
            auto_sync_channel_start=1.0,
            custom_properties={
                "stalker_genre_id": "10",
                "channel_numbering_mode": "provider",
            },
        )

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.m3u.tasks.cleanup_stale_group_relationships", return_value=0)
    @patch("apps.m3u.tasks.cleanup_streams", return_value=0)
    @patch("apps.m3u.tasks.refresh_m3u_groups")
    @patch("apps.m3u.tasks.StalkerClient.discover_live_channels")
    def test_refresh_auto_syncs_stalker_channels_and_keeps_them_in_sync(
        self,
        mock_discover_channels,
        mock_refresh_groups,
        _mock_cleanup_streams,
        _mock_cleanup_groups,
        _mock_update,
    ):
        mock_refresh_groups.return_value = (
            [],
            {
                "News": {"stalker_genre_id": "10"},
            },
        )
        mock_discover_channels.side_effect = [
            StalkerChannelDiscoveryResult(
                normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
                profile_name="Demo",
                genres=[{"id": "10", "title": "News"}],
                channels=[
                    {
                        "id": "5001",
                        "name": "World News",
                        "number": "501",
                        "cmd": "ffmpeg http://upstream.example.com/live/world-news",
                        "cmd_id": "1001",
                        "cmd_ch_id": "7001",
                        "genre_id": "10",
                        "genre_name": "News",
                        "logo_url": "http://portal.example.com/stalker_portal/misc/logos/320/world-news.png",
                        "xmltv_id": "world.news",
                    },
                ],
                token="TOKEN-456",
                used_authentication=True,
            ),
            StalkerChannelDiscoveryResult(
                normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
                profile_name="Demo",
                genres=[{"id": "10", "title": "News"}],
                channels=[
                    {
                        "id": "5001",
                        "name": "World News HD",
                        "number": "501",
                        "cmd": "ffmpeg http://upstream.example.com/live/world-news-hd",
                        "cmd_id": "1001",
                        "cmd_ch_id": "7001",
                        "genre_id": "10",
                        "genre_name": "News",
                        "logo_url": "http://portal.example.com/stalker_portal/misc/logos/320/world-news-hd.png",
                        "xmltv_id": "world.news",
                    },
                ],
                token="TOKEN-789",
                used_authentication=True,
            ),
            StalkerChannelDiscoveryResult(
                normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
                profile_name="Demo",
                genres=[{"id": "10", "title": "News"}],
                channels=[],
                token="TOKEN-999",
                used_authentication=True,
            ),
        ]

        _refresh_single_m3u_account_impl(self.account.id)

        channel = Channel.objects.get(auto_created_by=self.account)
        self.assertTrue(channel.auto_created)
        self.assertEqual(channel.channel_group, self.news_group)
        self.assertEqual(channel.channel_number, 501)
        self.assertEqual(channel.name, "World News")
        self.assertEqual(channel.tvg_id, "world.news")
        self.assertEqual(channel.logo.url, "http://portal.example.com/stalker_portal/misc/logos/320/world-news.png")

        channel_stream = ChannelStream.objects.get(channel=channel)
        original_stream_id = channel_stream.stream_id
        self.assertEqual(channel_stream.stream.m3u_account, self.account)
        self.assertEqual(channel_stream.stream.stream_chno, 501)
        self.assertEqual(channel_stream.stream.custom_properties["provider_type"], "stalker")

        _refresh_single_m3u_account_impl(self.account.id)

        channel.refresh_from_db()
        self.assertEqual(channel.id, Channel.objects.get(auto_created_by=self.account).id)
        self.assertEqual(channel.name, "World News HD")
        self.assertEqual(channel.channel_number, 501)
        self.assertEqual(channel.logo.url, "http://portal.example.com/stalker_portal/misc/logos/320/world-news-hd.png")
        self.assertEqual(
            ChannelStream.objects.get(channel=channel).stream_id,
            original_stream_id,
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.custom_properties["token"], "TOKEN-789")
        self.assertIn("Auto sync: 0 channels created, 1 updated, 0 deleted", self.account.last_message)

        _refresh_single_m3u_account_impl(self.account.id)

        self.assertFalse(Channel.objects.filter(auto_created_by=self.account).exists())
