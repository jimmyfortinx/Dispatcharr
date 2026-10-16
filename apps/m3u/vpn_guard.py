"""Keep the shared VPN exit IP usable for every provider.

Dispatcharr can share a gluetun VPN with its sidecars, so every provider sees
the same exit IP. VPN exit IPs get blocked by providers because of other VPN
users. This guard probes each active provider through the VPN. When one of
them rejects the exit IP and nothing is playing, it asks gluetun for a new
server and probes again. It runs as ``python manage.py vpn_guard``.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

PROBE_READ_BYTES = 64 * 1024
PROBE_STREAMS_PER_ACCOUNT = 2
BAD_IP_KEY_PREFIX = "vpn_guard:bad_ip:"

OK = "ok"
BLOCKED = "blocked"
ERROR = "error"


@dataclass
class GuardConfig:
    gluetun_url: str = "http://127.0.0.1:8000"
    gluetun_api_key: str = ""
    interval_seconds: int = 1800
    backoff_seconds: int = 3 * 3600
    startup_delay_seconds: int = 120
    max_rotations: int = 5
    rotation_timeout_seconds: int = 90
    bad_ip_ttl_seconds: int = 24 * 3600
    block_statuses: frozenset = field(default_factory=lambda: frozenset({456}))

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        defaults = cls()

        def as_int(name, default):
            raw = (env.get(name) or "").strip()
            return int(raw) if raw else default

        statuses_raw = (env.get("VPN_GUARD_BLOCK_STATUSES") or "").strip()
        block_statuses = (
            frozenset(int(s) for s in statuses_raw.split(",") if s.strip())
            if statuses_raw
            else defaults.block_statuses
        )
        return cls(
            gluetun_url=(env.get("VPN_GUARD_GLUETUN_URL") or defaults.gluetun_url).rstrip("/"),
            gluetun_api_key=_api_key_from_env(env),
            interval_seconds=as_int("VPN_GUARD_INTERVAL_SECONDS", defaults.interval_seconds),
            backoff_seconds=as_int("VPN_GUARD_BACKOFF_SECONDS", defaults.backoff_seconds),
            startup_delay_seconds=as_int(
                "VPN_GUARD_STARTUP_DELAY_SECONDS", defaults.startup_delay_seconds
            ),
            max_rotations=as_int("VPN_GUARD_MAX_ROTATIONS", defaults.max_rotations),
            rotation_timeout_seconds=as_int(
                "VPN_GUARD_ROTATION_TIMEOUT_SECONDS", defaults.rotation_timeout_seconds
            ),
            bad_ip_ttl_seconds=as_int("VPN_GUARD_BAD_IP_TTL_SECONDS", defaults.bad_ip_ttl_seconds),
            block_statuses=block_statuses,
        )


def _api_key_from_env(env):
    """Read the gluetun API key directly, or from gluetun's own role JSON.

    Reusing ``HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE`` lets the deployment point
    both containers at the same secret key.
    """
    api_key = (env.get("VPN_GUARD_GLUETUN_API_KEY") or "").strip()
    if api_key:
        return api_key
    role_raw = (env.get("HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE") or "").strip()
    if not role_raw:
        return ""
    try:
        role = json.loads(role_raw)
    except ValueError:
        logger.warning("HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE is not valid JSON")
        return ""
    return str(role.get("apikey") or "").strip() if isinstance(role, dict) else ""


class GluetunError(Exception):
    pass


class GluetunClient:
    """Minimal client for gluetun's HTTP control server."""

    def __init__(self, base_url, api_key="", timeout=10, sleep=time.sleep, clock=time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key
        self._sleep = sleep
        self._clock = clock

    def public_ip(self):
        response = self.session.get(f"{self.base_url}/v1/publicip/ip", timeout=self.timeout)
        response.raise_for_status()
        return str((response.json() or {}).get("public_ip") or "").strip()

    def _set_vpn_status(self, status):
        response = self.session.put(
            f"{self.base_url}/v1/vpn/status",
            json={"status": status},
            timeout=self.timeout,
        )
        response.raise_for_status()

    def rotate(self, previous_ip, timeout_seconds=90):
        """Restart the VPN so gluetun picks a new random server.

        Returns the new public IP. gluetun can report the old IP for a moment
        after the restart, so a different IP is awaited until the deadline. It
        can still equal ``previous_ip`` when gluetun picks the same server.
        """
        self._set_vpn_status("stopped")
        self._set_vpn_status("running")
        deadline = self._clock() + timeout_seconds
        last_ip = ""
        while self._clock() < deadline:
            self._sleep(3)
            try:
                last_ip = self.public_ip() or last_ip
            except requests.RequestException:
                continue
            if last_ip and last_ip != previous_ip:
                return last_ip
        if last_ip:
            return last_ip
        raise GluetunError(f"gluetun did not report a public IP within {timeout_seconds}s")


@dataclass
class ProbeResult:
    account_name: str
    outcome: str
    detail: str = ""


def _read_stream(url, headers, block_statuses):
    """Fetch the start of a stream and classify the answer."""
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=(5, 15))
    except requests.RequestException as exc:
        return ERROR, f"request failed: {exc.__class__.__name__}"
    try:
        if response.status_code in block_statuses:
            return BLOCKED, f"HTTP {response.status_code}"
        if response.status_code != 200:
            return ERROR, f"HTTP {response.status_code}"
        size = 0
        for chunk in response.iter_content(8192):
            size += len(chunk)
            if size >= PROBE_READ_BYTES:
                break
        if size == 0:
            return ERROR, "HTTP 200 without data"
        return OK, f"HTTP 200, {size} bytes"
    except requests.RequestException as exc:
        return ERROR, f"read failed: {exc.__class__.__name__}"
    finally:
        response.close()


def _resolve_probe_request(stream):
    """Build the upstream URL and headers the live proxy would use."""
    from apps.m3u.models import M3UAccount
    from apps.proxy.live_proxy.url_utils import (
        _resolve_live_stream_context,
        _resolve_live_stream_url,
    )

    account = stream.m3u_account
    context = _resolve_live_stream_context(stream)
    if account.account_type == M3UAccount.Types.STALKER:
        url = context["url"]
    else:
        profile = account.profiles.filter(is_default=True).first()
        url = _resolve_live_stream_url(stream, account, profile) if profile else context["url"]
    headers = dict(context.get("input_headers") or {})
    if context.get("user_agent") and "User-Agent" not in headers:
        headers["User-Agent"] = context["user_agent"]
    return url, headers


def _probe_streams(account):
    from apps.channels.models import Stream

    streams = Stream.objects.filter(m3u_account=account).select_related("m3u_account__user_agent")
    in_channels = list(streams.filter(channels__isnull=False).distinct().order_by("?")[:PROBE_STREAMS_PER_ACCOUNT])
    if in_channels:
        return in_channels
    return list(streams.order_by("?")[:PROBE_STREAMS_PER_ACCOUNT])


def probe_account(account, block_statuses):
    """Probe a few streams of one account. One readable stream is enough."""
    streams = _probe_streams(account)
    if not streams:
        return ProbeResult(account.name, ERROR, "no streams")

    details = []
    blocked = False
    for stream in streams:
        try:
            url, headers = _resolve_probe_request(stream)
        except Exception as exc:  # provider-specific resolution errors
            details.append(f"stream {stream.id}: resolve failed: {exc}")
            continue
        outcome, detail = _read_stream(url, headers, block_statuses)
        details.append(f"stream {stream.id}: {detail}")
        if outcome == OK:
            return ProbeResult(account.name, OK, "; ".join(details))
        blocked = blocked or outcome == BLOCKED
    return ProbeResult(account.name, BLOCKED if blocked else ERROR, "; ".join(details))


def active_accounts():
    from apps.m3u.models import M3UAccount

    return list(M3UAccount.objects.filter(is_active=True).order_by("id"))


def active_connection_count():
    from apps.proxy.utils import get_user_active_connections

    return len(get_user_active_connections(None))


class VpnGuard:
    def __init__(
        self,
        config: GuardConfig,
        gluetun: GluetunClient,
        redis_client=None,
        probe: Optional[Callable[[], Iterable[ProbeResult]]] = None,
        connection_count: Callable[[], int] = active_connection_count,
    ):
        self.config = config
        self.gluetun = gluetun
        self.redis = redis_client
        self._probe = probe or self._probe_all
        self._connection_count = connection_count

    def _probe_all(self):
        return [probe_account(a, self.config.block_statuses) for a in active_accounts()]

    def _mark_bad(self, ip):
        if ip and self.redis is not None:
            self.redis.set(f"{BAD_IP_KEY_PREFIX}{ip}", "1", ex=self.config.bad_ip_ttl_seconds)

    def _is_bad(self, ip):
        return bool(ip and self.redis is not None and self.redis.exists(f"{BAD_IP_KEY_PREFIX}{ip}"))

    def _blocked(self, results, ip):
        blocked = [r for r in results if r.outcome == BLOCKED]
        for result in results:
            log = logger.warning if result.outcome == BLOCKED else logger.info
            log("vpn_guard ip=%s account=%s outcome=%s %s", ip, result.account_name, result.outcome, result.detail)
        return blocked

    def run_cycle(self, rotate=True):
        """Probe once and rotate if needed. Returns seconds to wait before the next cycle."""
        busy = self._connection_count()
        if busy:
            logger.info("vpn_guard: %s active connection(s), skipping probe", busy)
            return self.config.interval_seconds

        ip = self.gluetun.public_ip()
        blocked = self._blocked(self._probe(), ip)
        if not blocked:
            logger.info("vpn_guard: exit IP %s works for every provider", ip)
            return self.config.interval_seconds
        if not rotate:
            return self.config.interval_seconds

        self._mark_bad(ip)
        for attempt in range(1, self.config.max_rotations + 1):
            busy = self._connection_count()
            if busy:
                logger.info("vpn_guard: %s active connection(s), stopping rotation", busy)
                return self.config.interval_seconds

            ip = self.gluetun.rotate(ip, self.config.rotation_timeout_seconds)
            logger.info("vpn_guard: rotation %s/%s, new exit IP %s", attempt, self.config.max_rotations, ip)
            if self._is_bad(ip):
                logger.info("vpn_guard: %s was blocked recently, rotating again", ip)
                continue

            blocked = self._blocked(self._probe(), ip)
            if not blocked:
                logger.info("vpn_guard: exit IP %s works for every provider", ip)
                return self.config.interval_seconds
            self._mark_bad(ip)

        logger.warning(
            "vpn_guard: still blocked after %s rotations (%s); waiting %ss. "
            "A provider may be refusing the account itself, not the IP.",
            self.config.max_rotations,
            ", ".join(r.account_name for r in blocked),
            self.config.backoff_seconds,
        )
        return self.config.backoff_seconds
