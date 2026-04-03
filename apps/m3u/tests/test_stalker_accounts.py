from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.m3u.serializers import M3UAccountSerializer
from apps.m3u.stalker import StalkerClient


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

    def create_payload(self, **overrides):
        payload = {
            "name": "New Stalker Provider",
            "account_type": M3UAccount.Types.STALKER,
            "server_url": "http://portal.example.com/c/",
            "mac": "00:1A:79:00:00:20",
            "username": "demo",
            "password": "secret",
        }
        payload.update(overrides)
        return payload

    @patch("apps.m3u.api_views.refresh_m3u_groups")
    def test_create_stalker_account_runs_initial_group_discovery(self, mock_refresh_groups):
        def fake_refresh(account_id):
            account = M3UAccount.objects.get(id=account_id)
            account.status = M3UAccount.Status.PENDING_SETUP
            account.last_message = (
                "M3U groups loaded. Please select groups or refresh M3U to complete setup."
            )
            account.save(update_fields=["status", "last_message"])
            return [], {"News": {"stalker_genre_id": "1"}}

        mock_refresh_groups.side_effect = fake_refresh
        response = self.client.post(
            "/api/m3u/accounts/",
            self.create_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = M3UAccount.objects.get(id=response.data["id"])
        self.assertEqual(created.status, M3UAccount.Status.PENDING_SETUP)
        self.assertIn("Please select groups", created.last_message)
        mock_refresh_groups.assert_called_once_with(created.id)

    @patch("apps.m3u.api_views.refresh_m3u_groups")
    def test_create_stalker_account_persists_error_when_initial_discovery_fails(
        self,
        mock_refresh_groups,
    ):
        def fake_refresh(account_id):
            account = M3UAccount.objects.get(id=account_id)
            account.status = M3UAccount.Status.ERROR
            account.last_message = "Portal rejected the provided credentials."
            account.save(update_fields=["status", "last_message"])
            return "Portal rejected the provided credentials.", None

        mock_refresh_groups.side_effect = fake_refresh

        response = self.client.post(
            "/api/m3u/accounts/",
            self.create_payload(name="Broken Stalker Provider"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = M3UAccount.objects.get(id=response.data["id"])
        self.assertEqual(created.status, M3UAccount.Status.ERROR)
        self.assertEqual(
            created.last_message, "Portal rejected the provided credentials."
        )
        mock_refresh_groups.assert_called_once_with(created.id)

    @patch("apps.vod.tasks.refresh_categories")
    @patch("apps.m3u.api_views.ensure_default_vod_category_relations")
    @patch("apps.m3u.api_views.refresh_m3u_groups")
    def test_create_vod_enabled_stalker_account_preloads_vod_categories(
        self,
        mock_refresh_groups,
        mock_ensure_relations,
        mock_refresh_categories,
    ):
        def fake_refresh(account_id):
            account = M3UAccount.objects.get(id=account_id)
            account.status = M3UAccount.Status.PENDING_SETUP
            account.last_message = "M3U groups loaded."
            account.save(update_fields=["status", "last_message"])
            return [], {"News": {"stalker_genre_id": "1"}}

        mock_refresh_groups.side_effect = fake_refresh

        response = self.client.post(
            "/api/m3u/accounts/",
            self.create_payload(
                name="Stalker With VOD",
                enable_vod=True,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = M3UAccount.objects.get(id=response.data["id"])
        mock_refresh_groups.assert_called_once_with(created.id)
        mock_ensure_relations.assert_called_once()
        mock_refresh_categories.assert_called_once_with(created.id)

    def test_removed_test_connection_endpoint_returns_404(self):
        response = self.client.post(
            f"/api/m3u/accounts/{self.account.id}/test-connection/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
