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


from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.m3u.stalker import (
    StalkerClient,
    StalkerConnectionResult,
    StalkerError,
)


User = get_user_model()


class StalkerPhase1ClientTests(TestCase):
    def test_normalize_portal_candidates_from_c_path(self):
        candidates = StalkerClient.normalize_portal_candidates(
            "http://portal.example.com/stalker_portal/c/"
        )

        self.assertEqual(
            candidates[:2],
            [
                "http://portal.example.com/stalker_portal/server/load.php",
                "http://portal.example.com/stalker_portal/portal.php",
            ],
        )

    def test_normalize_portal_candidates_from_root(self):
        candidates = StalkerClient.normalize_portal_candidates(
            "http://portal.example.com"
        )

        self.assertIn(
            "http://portal.example.com/stalker_portal/server/load.php", candidates
        )
        self.assertIn("http://portal.example.com/server/load.php", candidates)


class StalkerPhase1APITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.account = M3UAccount.objects.create(
            name="Stalker API",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={"mac": "00:1A:79:00:00:10"},
        )

    @patch("apps.m3u.api_views.StalkerClient.test_connection")
    def test_test_connection_success_updates_status_and_message(self, mock_test):
        mock_test.return_value = StalkerConnectionResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo User",
            genre_count=12,
            token="ABC123TOKEN",
            used_authentication=True,
        )

        response = self.client.post(
            f"/api/m3u/accounts/{self.account.id}/test-connection/", format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, M3UAccount.Status.SUCCESS)
        self.assertIn("Retrieved 12 live genres", self.account.last_message)
        self.assertEqual(self.account.custom_properties["token"], "ABC123TOKEN")
        self.assertEqual(response.data["account"]["status"], M3UAccount.Status.SUCCESS)

    @patch("apps.m3u.api_views.StalkerClient.test_connection")
    def test_test_connection_failure_updates_error_status(self, mock_test):
        mock_test.side_effect = StalkerError("Portal rejected the provided credentials.")

        response = self.client.post(
            f"/api/m3u/accounts/{self.account.id}/test-connection/", format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, M3UAccount.Status.ERROR)
        self.assertEqual(
            self.account.last_message, "Portal rejected the provided credentials."
        )

    def test_test_connection_rejects_non_stalker_accounts(self):
        standard = M3UAccount.objects.create(
            name="Standard API",
            account_type=M3UAccount.Types.STADNARD,
            server_url="http://playlist.example.com/list.m3u",
        )

        response = self.client.post(
            f"/api/m3u/accounts/{standard.id}/test-connection/", format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
