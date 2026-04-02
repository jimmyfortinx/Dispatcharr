"""Regression tests for StreamGenerator initialization waiting."""

from itertools import chain, repeat
from unittest.mock import MagicMock, patch

from django.test import TestCase


def _make_generator():
    from apps.proxy.ts_proxy.stream_generator import StreamGenerator

    gen = StreamGenerator.__new__(StreamGenerator)
    gen.channel_id = "00000000-0000-0000-0000-000000000123"
    gen.client_id = "test-client-init"
    gen.client_ip = "127.0.0.1"
    gen.client_user_agent = "TestUA/1.0"
    gen.channel_initializing = True
    gen.stream_start_time = 0
    gen.bytes_sent = 0
    gen.chunks_sent = 0
    return gen


class StreamGeneratorInitializationTests(TestCase):
    def test_wait_for_initialization_sends_keepalive_packet_for_string_status(self):
        gen = _make_generator()

        redis_client = MagicMock()
        redis_client.hgetall.side_effect = [
            {"state": "initializing", "init_time": "999.0"},
            {"state": "waiting_for_clients"},
        ]
        redis_client.exists.return_value = False

        proxy_server = MagicMock()
        proxy_server.redis_client = redis_client

        time_values = chain([1000.0, 1000.0, 1000.1, 1000.6, 1000.6, 1000.7], repeat(1000.7))

        with patch("apps.proxy.ts_proxy.stream_generator.ProxyServer.get_instance", return_value=proxy_server), patch(
            "apps.proxy.ts_proxy.stream_generator.gevent.sleep"
        ), patch(
            "apps.proxy.ts_proxy.stream_generator.time.time",
            side_effect=time_values,
        ):
            packets = list(gen._wait_for_initialization())

        self.assertEqual(len(packets), 1)
        self.assertIsInstance(packets[0], bytes)
        self.assertEqual(len(packets[0]), 188)
        self.assertIn(b"Initializing: initializing", packets[0])

    def test_generate_yields_initialization_packets_before_streaming(self):
        gen = _make_generator()

        def wait_for_initialization():
            yield b"init-packet"
            return True

        with patch.object(gen, "_wait_for_initialization", side_effect=wait_for_initialization), patch.object(
            gen, "_setup_streaming", return_value=False
        ) as mock_setup, patch.object(gen, "_cleanup"):
            packets = list(gen.generate())

        self.assertEqual(packets, [b"init-packet"])
        mock_setup.assert_called_once()

    def test_generate_stops_when_initialization_fails(self):
        gen = _make_generator()

        def wait_for_initialization():
            yield b"error-packet"
            return False

        with patch.object(gen, "_wait_for_initialization", side_effect=wait_for_initialization), patch.object(
            gen, "_setup_streaming"
        ) as mock_setup, patch.object(gen, "_cleanup"):
            packets = list(gen.generate())

        self.assertEqual(packets, [b"error-packet"])
        mock_setup.assert_not_called()
