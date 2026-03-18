from unittest.mock import patch

from django.test import TestCase

from apps.channels.models import ChannelGroupM3UAccount
from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerGenreDiscoveryResult
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
    @patch("apps.m3u.tasks.StalkerClient.discover_live_genres")
    def test_refresh_groups_persists_stalker_categories(
        self,
        mock_discover,
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
    @patch("apps.m3u.tasks.StalkerClient.discover_live_genres")
    def test_refresh_groups_preserves_existing_relation_metadata(
        self,
        mock_discover,
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
