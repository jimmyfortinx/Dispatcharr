from unittest.mock import patch

from django.test import TestCase

from apps.channels.models import ChannelGroupM3UAccount
from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerAccountInfoResult, StalkerGenreDiscoveryResult
from apps.m3u.tasks import refresh_m3u_groups, refresh_single_m3u_account


class StalkerPhase2GroupDiscoveryTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Groups",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={
                "mac": "00:1A:79:00:00:20",
                "existing_key": "keep-me",
            },
        )

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.m3u.tasks.release_task_lock")
    @patch("apps.m3u.tasks.TaskLockRenewer")
    @patch("apps.m3u.tasks.acquire_task_lock", return_value=True)
    @patch("apps.m3u.tasks.StalkerClient.discover_account_info")
    @patch("apps.m3u.tasks.StalkerClient.discover_live_genres")
    def test_refresh_groups_persists_stalker_categories(
        self,
        mock_discover,
        mock_discover_account_info,
        _mock_lock,
        mock_renewer_cls,
        _mock_release,
        _mock_update,
    ):
        mock_renewer = mock_renewer_cls.return_value
        mock_discover.return_value = StalkerGenreDiscoveryResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            genres=[
                {"id": "10", "title": "News"},
                {"id": 11, "title": "Sports"},
            ],
            token="TOKEN-123",
            used_authentication=True,
        )
        mock_discover_account_info.return_value = StalkerAccountInfoResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            account_info={
                "last_refresh": "2026-03-28T12:00:00Z",
                "auth_timestamp": 1711627200,
                "user_info": {"status": "Active", "exp_date": "1893456000"},
                "server_info": {
                    "url": "http://portal.example.com/stalker_portal/server/load.php",
                    "timezone": "America/Toronto",
                },
            },
            token="TOKEN-123",
            used_authentication=True,
        )

        extinf_data, groups = refresh_m3u_groups(self.account.id)

        self.assertEqual(extinf_data, [])
        self.assertEqual(
            groups,
            {
                "News": {"stalker_genre_id": "10"},
                "Sports": {"stalker_genre_id": "11"},
            },
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.status, M3UAccount.Status.PENDING_SETUP)
        self.assertEqual(self.account.custom_properties["token"], "TOKEN-123")
        self.assertEqual(self.account.custom_properties["existing_key"], "keep-me")

        relations = {
            rel.channel_group.name: rel
            for rel in ChannelGroupM3UAccount.objects.filter(m3u_account=self.account)
            .select_related("channel_group")
        }
        self.assertEqual(relations["News"].custom_properties["stalker_genre_id"], "10")
        self.assertEqual(
            relations["Sports"].custom_properties["stalker_genre_id"], "11"
        )
        mock_renewer.start.assert_called_once()
        mock_renewer.stop.assert_called_once()

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.m3u.tasks.release_task_lock")
    @patch("apps.m3u.tasks.TaskLockRenewer")
    @patch("apps.m3u.tasks.acquire_task_lock", return_value=True)
    @patch("apps.m3u.tasks.StalkerClient.discover_account_info")
    @patch("apps.m3u.tasks.StalkerClient.discover_live_genres")
    def test_refresh_groups_preserves_existing_relation_metadata(
        self,
        mock_discover,
        mock_discover_account_info,
        _mock_lock,
        _mock_renewer_cls,
        _mock_release,
        _mock_update,
    ):
        mock_discover.return_value = StalkerGenreDiscoveryResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            genres=[{"id": "10", "title": "News"}],
            token="TOKEN-123",
            used_authentication=True,
        )
        mock_discover_account_info.return_value = StalkerAccountInfoResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            account_info={
                "last_refresh": "2026-03-28T12:00:00Z",
                "auth_timestamp": 1711627200,
                "user_info": {"status": "Active", "exp_date": "1893456000"},
                "server_info": {
                    "url": "http://portal.example.com/stalker_portal/server/load.php",
                    "timezone": "America/Toronto",
                },
            },
            token="TOKEN-123",
            used_authentication=True,
        )
        refresh_m3u_groups(self.account.id)
        relation = ChannelGroupM3UAccount.objects.get(
            m3u_account=self.account,
            channel_group__name="News",
        )
        relation.custom_properties = {
            "stalker_genre_id": "10",
            "custom_logo_id": 42,
        }
        relation.save(update_fields=["custom_properties"])

        mock_discover.return_value = StalkerGenreDiscoveryResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            genres=[{"id": "15", "title": "News"}],
            token="TOKEN-456",
            used_authentication=True,
        )
        mock_discover_account_info.return_value = StalkerAccountInfoResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            account_info={
                "last_refresh": "2026-03-28T12:05:00Z",
                "auth_timestamp": 1711627500,
                "user_info": {"status": "Active", "exp_date": "1893456000"},
                "server_info": {
                    "url": "http://portal.example.com/stalker_portal/server/load.php",
                    "timezone": "America/Toronto",
                },
            },
            token="TOKEN-456",
            used_authentication=True,
        )

        refresh_m3u_groups(self.account.id)

        relation.refresh_from_db()
        self.assertEqual(relation.custom_properties["stalker_genre_id"], "15")
        self.assertEqual(relation.custom_properties["custom_logo_id"], 42)

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.m3u.tasks.release_task_lock")
    @patch("apps.m3u.tasks.TaskLockRenewer")
    @patch("apps.m3u.tasks.acquire_task_lock", return_value=True)
    @patch("apps.m3u.tasks.refresh_m3u_groups")
    def test_full_refresh_stops_after_group_discovery_for_stalker(
        self,
        mock_refresh_groups,
        _mock_lock,
        _mock_renewer_cls,
        _mock_release,
        _mock_update,
    ):
        mock_refresh_groups.return_value = (
            [],
            {
                "News": {"stalker_genre_id": "10"},
                "Sports": {"stalker_genre_id": "11"},
            },
        )

        result = refresh_single_m3u_account(self.account.id)

        self.assertEqual(result, "Stalker group discovery complete.")
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, M3UAccount.Status.PENDING_SETUP)
        self.assertIn("Discovered 2 Stalker live groups", self.account.last_message)
        mock_refresh_groups.assert_called_once()


from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.channels.models import ChannelGroup, ChannelGroupM3UAccount
from apps.m3u.models import M3UAccount


User = get_user_model()


class StalkerPhase3GroupSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.account = M3UAccount.objects.create(
            name="Stalker Groups",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:30"},
        )
        self.group = ChannelGroup.objects.create(name="News")
        self.relation = ChannelGroupM3UAccount.objects.create(
            channel_group=self.group,
            m3u_account=self.account,
            enabled=True,
            auto_channel_sync=False,
            auto_sync_channel_start=1.0,
            custom_properties={
                "stalker_genre_id": "10",
                "custom_epg_id": 99,
            },
        )

    def test_group_settings_update_preserves_stalker_genre_id(self):
        response = self.client.patch(
            f"/api/m3u/accounts/{self.account.id}/group-settings/",
            {
                "group_settings": [
                    {
                        "channel_group": self.group.id,
                        "enabled": False,
                        "auto_channel_sync": True,
                        "auto_sync_channel_start": 101,
                        "custom_properties": {
                            "custom_epg_id": 123,
                        },
                    }
                ],
                "category_settings": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.relation.refresh_from_db()
        self.assertFalse(self.relation.enabled)
        self.assertTrue(self.relation.auto_channel_sync)
        self.assertEqual(self.relation.auto_sync_channel_start, 101)
        self.assertEqual(self.relation.custom_properties["stalker_genre_id"], "10")
        self.assertEqual(self.relation.custom_properties["custom_epg_id"], 123)


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
