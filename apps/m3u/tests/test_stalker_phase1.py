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
