from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


def _make_generator(buffer_index=20):
    from apps.proxy.live_proxy.output.ts.generator import StreamGenerator

    gen = StreamGenerator.__new__(StreamGenerator)
    gen.channel_id = "00000000-0000-0000-0000-000000000123"
    gen.client_id = "test-client-live-start"
    gen._source_buffer = MagicMock()
    gen._source_buffer.index = buffer_index
    gen.proxy_server = None
    return gen


class StreamGeneratorLiveStartTests(SimpleTestCase):
    def test_live_start_uses_initial_behind_margin(self):
        gen = _make_generator(buffer_index=20)
        proxy_server = MagicMock()
        proxy_server.stream_managers = {gen.channel_id: MagicMock()}
        proxy_server.am_i_owner.return_value = True

        with patch(
            "apps.proxy.live_proxy.output.ts.generator.ProxyServer.get_instance",
            return_value=proxy_server,
        ), patch(
            "apps.proxy.live_proxy.output.ts.generator.ConfigHelper.new_client_behind_seconds",
            return_value=0,
        ), patch(
            "apps.proxy.live_proxy.output.ts.generator.ConfigHelper.initial_behind_chunks",
            return_value=4,
        ):
            self.assertTrue(gen._setup_streaming())

        self.assertEqual(gen.local_index, 16)

    def test_live_start_clamps_to_zero_when_buffer_is_short(self):
        gen = _make_generator(buffer_index=2)
        proxy_server = MagicMock()
        proxy_server.stream_managers = {gen.channel_id: MagicMock()}
        proxy_server.am_i_owner.return_value = True

        with patch(
            "apps.proxy.live_proxy.output.ts.generator.ProxyServer.get_instance",
            return_value=proxy_server,
        ), patch(
            "apps.proxy.live_proxy.output.ts.generator.ConfigHelper.new_client_behind_seconds",
            return_value=0,
        ), patch(
            "apps.proxy.live_proxy.output.ts.generator.ConfigHelper.initial_behind_chunks",
            return_value=4,
        ):
            self.assertTrue(gen._setup_streaming())

        self.assertEqual(gen.local_index, 0)
