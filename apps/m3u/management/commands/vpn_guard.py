import logging
import time

from django.core.management.base import BaseCommand
from django.db import connections

from apps.m3u.vpn_guard import GluetunClient, GuardConfig, VpnGuard
from core.utils import RedisClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Rotate the gluetun VPN server when a provider blocks the exit IP"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Probe providers and log the results without rotating the VPN",
        )

    def handle(self, *args, **options):
        config = GuardConfig.from_env()
        guard = VpnGuard(
            config,
            GluetunClient(config.gluetun_url, config.gluetun_api_key),
            redis_factory=RedisClient.get_client,
        )
        rotate = not options["dry_run"]

        if options["once"]:
            guard.run_cycle(rotate=rotate)
            return

        logger.info("vpn_guard: starting, first probe in %ss", config.startup_delay_seconds)
        time.sleep(config.startup_delay_seconds)
        while True:
            try:
                wait = guard.run_cycle(rotate=rotate)
            except Exception:
                logger.exception("vpn_guard: cycle failed")
                wait = config.interval_seconds
            finally:
                # Cycles are 30 min apart: start each one with a fresh database
                # connection instead of one Postgres may have closed meanwhile.
                connections.close_all()
            time.sleep(wait)
