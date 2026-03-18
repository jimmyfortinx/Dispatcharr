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
