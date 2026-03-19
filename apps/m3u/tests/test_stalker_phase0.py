from unittest.mock import patch

from django.test import TestCase

from apps.m3u.models import M3UAccount
from apps.m3u.serializers import M3UAccountSerializer


class StalkerPhase0SerializerTests(TestCase):
    def test_stalker_create_persists_custom_properties_fields(self):
        serializer = M3UAccountSerializer(
            data={
                "name": "Stalker Portal",
                "account_type": M3UAccount.Types.STALKER,
                "server_url": "http://portal.example.com/c/",
                "mac": "00:1A:79:00:00:01",
                "username": "demo",
                "password": "secret",
                "timezone": "America/Toronto",
                "custom_properties": {"existing_key": "keep-me"},
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        account = serializer.save()

        self.assertEqual(account.account_type, M3UAccount.Types.STALKER)
        self.assertEqual(
            account.custom_properties["mac"], "00:1A:79:00:00:01"
        )
        self.assertEqual(account.custom_properties["timezone"], "America/Toronto")
        self.assertEqual(account.custom_properties["existing_key"], "keep-me")

        data = M3UAccountSerializer(account).data
        self.assertEqual(data["mac"], "00:1A:79:00:00:01")
        self.assertEqual(data["timezone"], "America/Toronto")

    def test_stalker_requires_portal_url_and_mac(self):
        serializer = M3UAccountSerializer(
            data={
                "name": "Broken Stalker",
                "account_type": M3UAccount.Types.STALKER,
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("server_url", serializer.errors)
        self.assertIn("mac", serializer.errors)

    def test_stalker_update_preserves_other_custom_properties(self):
        account = M3UAccount.objects.create(
            name="Existing Stalker",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/stalker_portal/c/",
            custom_properties={
                "existing_key": "keep-me",
                "enable_vod": False,
                "mac": "00:1A:79:00:00:02",
            },
        )

        serializer = M3UAccountSerializer(
            account,
            data={
                "name": account.name,
                "account_type": M3UAccount.Types.STALKER,
                "server_url": account.server_url,
                "mac": "00:1A:79:00:00:03",
                "device_id": "device-1",
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = serializer.save()

        self.assertEqual(updated.custom_properties["existing_key"], "keep-me")
        self.assertEqual(updated.custom_properties["mac"], "00:1A:79:00:00:03")
        self.assertEqual(updated.custom_properties["device_id"], "device-1")

    def test_stalker_update_preserves_existing_password_when_left_blank(self):
        account = M3UAccount.objects.create(
            name="Password Stalker",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/stalker_portal/c/",
            username="demo",
            password="secret",
            custom_properties={"mac": "00:1A:79:00:00:05"},
        )

        serializer = M3UAccountSerializer(
            account,
            data={
                "name": account.name,
                "account_type": M3UAccount.Types.STALKER,
                "server_url": account.server_url,
                "username": account.username,
                "password": "",
                "mac": "00:1A:79:00:00:05",
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = serializer.save()

        self.assertEqual(updated.password, "secret")


class StalkerPhase0SignalTests(TestCase):
    @patch("apps.m3u.signals.refresh_m3u_groups.delay")
    def test_creating_stalker_account_does_not_auto_refresh_groups(self, mock_delay):
        M3UAccount.objects.create(
            name="Signal Stalker",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:04"},
        )

        mock_delay.assert_not_called()

    @patch("apps.m3u.signals.refresh_m3u_groups.delay")
    def test_creating_standard_account_still_auto_refreshes_groups(self, mock_delay):
        M3UAccount.objects.create(
            name="Signal Standard",
            account_type=M3UAccount.Types.STADNARD,
            server_url="http://playlist.example.com/list.m3u",
        )

        mock_delay.assert_called_once()
