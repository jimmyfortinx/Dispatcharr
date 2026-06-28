"""
VOD (Video on Demand) proxy views for handling movie and series streaming.
Supports M3U profiles for authentication and URL transformation.
"""

import base64
import time
import random
import logging
import hashlib
import requests
from urllib.parse import urlencode, urlparse
from django.db import close_old_connections
from django.http import JsonResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from apps.vod.models import Movie, Series, Episode, M3UMovieRelation, M3UEpisodeRelation
from apps.vod.resolvers import resolve_vod_stream_context
from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.proxy.vod_proxy.multi_worker_connection_manager import (
    MultiWorkerVODConnectionManager,
    RedisBackedVODConnection,
    infer_content_type_from_url,
    get_vod_client_stop_key,
)
from .utils import get_client_info, create_vod_response
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from apps.accounts.models import User
from apps.accounts.permissions import IsAdmin
from rest_framework_simplejwt.authentication import JWTAuthentication
from apps.accounts.authentication import ApiKeyAuthentication, QueryParamJWTAuthentication
from apps.proxy.utils import check_user_stream_limits
from dispatcharr.utils import network_access_allowed
from core.utils import RedisClient, dispatcharr_user_agent

logger = logging.getLogger(__name__)

_request_times = {}
_probe_activity = {}
PROBE_ACTIVITY_WINDOW_SECONDS = 30
PROBE_ACTIVITY_MIN_UNIQUE_CONTENT = 3
PROBE_ACTIVITY_REDIS_KEY_PREFIX = "vod_probe_activity"
_SYNTHETIC_MP4_PROBE_SAMPLE_B64 = "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMUbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAj90cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAAAAABAAAAAAG3bWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAABAAAAAQABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABYm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAASJzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2Mi4xMS4xMDAgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAAg8SJZYAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAABYoAAAAAAAAABhzdHRzAAAAAAAAAAEAAAABAABAAAAAABxzdHNjAAAAAAAAAAEAAAABAAAAAQAAAAEAAAAUc3RzegAAAAAAAALFAAAAAQAAABRzdGNvAAAAAAAAAAEAAANEAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2Mi4zLjEwMAAAAAhmcmVlAAACzW1kYXQAAAKtBgX//6ncRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY1IHIzMjIyIGIzNTYwNWEgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDI1IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49MSBzY2VuZWN1dD00MCBpbnRyYV9yZWZyZXNoPTAgcmNfbG9va2FoZWFkPTQwIHJjPWNyZiBtYnRyZWU9MSBjcmY9MjMuMCBxY29tcD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0xOjEuMDAAgAAAABBliIQAFf/+98nvwKbr29+B"
_SYNTHETIC_MATROSKA_PROBE_SAMPLE_B64 = (
    "GkXfo6NChoEBQveBAULygQRC84EIQoKIbWF0cm9za2FCh4EEQoWBAhhTgGcBAAAAAAAFChFNm3TAv4QmWQZVTbuLU6uEFUmpZlOsgaFN"
    "u4tTq4QWVK5rU6yB7027jFOrhBJUw2dTrIIBi027jFOrhBxTu2tTrIIE7uwBAAAAAAAAUwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAFUmpZsm/hJ4ecLoq17GDD0JATY"
    "CMTGF2ZjYyLjMuMTAwV0GMTGF2ZjYyLjMuMTAwc6SQdBdbUm0/s9O6NzthMfNXT0SJiECPQAAAAAAAFlSua0CWv4Sd9hgbrgEAAAAAAA"
    "CH14EBc8WIUsspEVcoVjmcgQAitZyDdW5kiIEAho9WX01QRUc0L0lTTy9BVkODgQEj44OEO5rKAOCQsIEQuoEQmoECVbCEVbmBAVXugQ"
    "DsAQAAAAAAAAIAAGOirAFkAAr/4QAXZ2QACqzZXsBEAAADAAQAAAMACDxIllgBAAZo6+PLIsD9+PgAElTDZ0CCv4R0UjZ3c3OfY8CAZ8"
    "iZRaOHRU5DT0RFUkSHjExhdmY2Mi4zLjEwMHNz12PAi2PFiFLLKRFXKFY5Z8iiRaOHRU5DT0RFUkSHlUxhdmM2Mi4xMS4xMDAgbGlieD"
    "I2NGfIoUWjiERVUkFUSU9ORIeTMDA6MDA6MDEuMDAwMDAwMDAwAB9DtnVC1b+EKxqLiueBAKNCyYEAAIAAAAKtBgX//6ncRem95tlIt5"
    "Ys2CDZI+7veDI2NCAtIGNvcmUgMTY1IHIzMjIyIGIzNTYwNWEgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy"
    "0yMDI1IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MD"
    "owIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ"
    "2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFf"
    "cXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGlu"
    "dGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0x"
    "IGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49MSBzY2Vu"
    "ZWN1dD00MCBpbnRyYV9yZWZyZXNoPTAgcmNfbG9va2FoZWFkPTQwIHJjPWNyZiBtYnRyZWU9MSBjcmY9MjMuMCBxY29tcD0wLjYwIHFw"
    "bWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0xOjEuMDAAgAAAABBliIQAFf/+98nvwKbr29+BHFO7a5e/hFIp"
    "nD27j7OBALeK94EB8YICE/CBCQ=="
)


def _is_plex_probe_user_agent(user_agent):
    if not user_agent:
        return False
    normalized = user_agent.lower()
    return "lavf/" in normalized or "plex" in normalized


def _is_plex_scan_request(request, user_agent=None):
    normalized_user_agent = (user_agent or request.META.get("HTTP_USER_AGENT") or "").lower()
    if "lavf/" in normalized_user_agent or "plex" in normalized_user_agent:
        return True

    if request.META.get("HTTP_X_PLEX_PRODUCT") or request.META.get("HTTP_X_PLEX_CLIENT_IDENTIFIER"):
        return True

    return False


def _is_open_ended_probe_range(range_header):
    if not range_header:
        return False

    normalized = str(range_header).strip().lower()
    if not normalized.startswith("bytes="):
        return False

    range_spec = normalized[len("bytes="):]
    if "," in range_spec:
        return False
    if "-" not in range_spec:
        return False

    start, end = range_spec.split("-", 1)
    if end.strip():
        return False

    start = start.strip()
    return start.isdigit() or start == ""


def _record_probe_activity(client_ip, client_user_agent, content_type, content_id, now=None):
    now = now or time.time()
    activity_key = _get_probe_activity_redis_key(client_ip, client_user_agent)
    activity_member = f"{content_type}:{content_id}"
    redis_client = RedisClient.get_client()

    if redis_client is not None:
        try:
            cutoff = now - PROBE_ACTIVITY_WINDOW_SECONDS
            pipe = redis_client.pipeline(transaction=False)
            pipe.zadd(activity_key, {activity_member: now})
            pipe.zremrangebyscore(activity_key, "-inf", cutoff)
            pipe.zcard(activity_key)
            pipe.expire(activity_key, PROBE_ACTIVITY_WINDOW_SECONDS)
            _, _, unique_content_count, _ = pipe.execute()
            return {
                "unique_content_count": int(unique_content_count),
                "backend": "redis",
            }
        except Exception:
            logger.warning(
                "[VOD-PROBE] Redis probe activity tracking failed for %s",
                activity_key,
                exc_info=True,
            )

    fallback_key = f"{client_ip}:{client_user_agent or 'unknown'}"
    bucket = _probe_activity.setdefault(fallback_key, {})
    bucket[activity_member] = now
    cutoff = now - PROBE_ACTIVITY_WINDOW_SECONDS
    expired_members = [
        member for member, timestamp in bucket.items()
        if timestamp <= cutoff
    ]
    for member in expired_members:
        bucket.pop(member, None)
    return {
        "unique_content_count": len(bucket),
        "backend": "memory",
    }


def _get_probe_activity_redis_key(client_ip, client_user_agent):
    fingerprint = hashlib.sha1(
        f"{client_ip}:{client_user_agent or 'unknown'}".encode("utf-8")
    ).hexdigest()
    return f"{PROBE_ACTIVITY_REDIS_KEY_PREFIX}:{fingerprint}"


def _should_use_probe_mode(
    *,
    client_ip,
    client_user_agent,
    content_type,
    content_id,
    range_header,
    session_id,
    offset=None,
    utc_start=None,
    utc_end=None,
):
    evaluation = _evaluate_probe_mode(
        client_ip=client_ip,
        client_user_agent=client_user_agent,
        content_type=content_type,
        content_id=content_id,
        range_header=range_header,
        session_id=session_id,
        offset=offset,
        utc_start=utc_start,
        utc_end=utc_end,
    )
    return evaluation["enabled"]


def _evaluate_probe_mode(
    *,
    client_ip,
    client_user_agent,
    content_type,
    content_id,
    range_header,
    session_id,
    offset=None,
    utc_start=None,
    utc_end=None,
):
    if session_id or offset or utc_start or utc_end:
        return {
            "enabled": False,
            "reason": "existing-session-or-timeshift",
            "backend": None,
            "unique_content_count": None,
        }
    if not _is_plex_probe_user_agent(client_user_agent):
        return {
            "enabled": False,
            "reason": "non-plex-user-agent",
            "backend": None,
            "unique_content_count": None,
        }
    if not _is_open_ended_probe_range(range_header):
        return {
            "enabled": False,
            "reason": "range-not-open-ended",
            "backend": None,
            "unique_content_count": None,
        }

    activity = _record_probe_activity(
        client_ip,
        client_user_agent,
        content_type,
        content_id,
    )
    unique_content_count = activity["unique_content_count"]
    enabled = unique_content_count >= PROBE_ACTIVITY_MIN_UNIQUE_CONTENT
    return {
        "enabled": enabled,
        "reason": "scan-burst-detected" if enabled else "below-unique-content-threshold",
        "backend": activity["backend"],
        "unique_content_count": unique_content_count,
    }


def _iter_probe_metadata_dicts(content_obj, relation):
    relation_props = dict(getattr(relation, "custom_properties", None) or {})
    content_props = dict(getattr(content_obj, "custom_properties", None) or {})

    for payload in (
        relation_props.get("detailed_info"),
        relation_props.get("movie_data"),
        relation_props.get("detail_data"),
        relation_props.get("basic_data"),
        relation_props.get("info"),
        relation_props,
        content_props,
    ):
        if isinstance(payload, dict):
            yield payload


def _estimate_probe_bitrate_bps(content_obj, relation):
    for payload in _iter_probe_metadata_dicts(content_obj, relation):
        raw_bitrate = payload.get("bitrate")
        try:
            bitrate = float(raw_bitrate)
        except (TypeError, ValueError):
            bitrate = None
        if bitrate and bitrate > 0:
            if bitrate >= 50000:
                return int(bitrate)
            if bitrate >= 100:
                return int(bitrate * 1000)
            return int(bitrate * 1000 * 1000)

        video_info = payload.get("video")
        if isinstance(video_info, dict):
            for key in ("bitrate", "bit_rate"):
                try:
                    video_bitrate = float(video_info.get(key))
                except (TypeError, ValueError):
                    video_bitrate = None
                if video_bitrate and video_bitrate > 0:
                    if video_bitrate >= 50000:
                        return int(video_bitrate)
                    if video_bitrate >= 100:
                        return int(video_bitrate * 1000)
                    return int(video_bitrate * 1000 * 1000)
    return None


def _estimate_probe_content_length(content_obj, relation):
    duration_secs = getattr(content_obj, "duration_secs", None) or 0
    try:
        duration_secs = int(duration_secs)
    except (TypeError, ValueError):
        duration_secs = 0

    if duration_secs <= 0:
        duration_secs = 7200 if hasattr(relation, "movie_id") else 2400

    bitrate_bps = _estimate_probe_bitrate_bps(content_obj, relation)
    if bitrate_bps is None:
        bitrate_bps = 6_000_000 if hasattr(relation, "movie_id") else 3_000_000

    estimated_size = int((duration_secs * bitrate_bps) / 8)
    minimum_size = 32 * 1024 * 1024
    maximum_size = 50 * 1024 * 1024 * 1024
    return max(minimum_size, min(estimated_size, maximum_size))


def _get_probe_container_extension(relation):
    extension = getattr(relation, "container_extension", None)
    if extension:
        normalized = str(extension).strip().lower().lstrip(".")
        if normalized:
            return normalized
    return "mp4"


def _get_synthetic_probe_header_bytes(extension):
    normalized = (extension or "mp4").lower()
    if normalized in {"mp4", "m4v", "mov"}:
        return base64.b64decode(_SYNTHETIC_MP4_PROBE_SAMPLE_B64)
    if normalized in {"mkv", "webm"}:
        return base64.b64decode(_SYNTHETIC_MATROSKA_PROBE_SAMPLE_B64)
    if normalized == "avi":
        return b"RIFF\x24\x00\x00\x00AVI LIST"
    if normalized == "ts":
        return bytes([0x47]) + (b"\x00" * 187)
    if normalized in {"mpg", "mpeg"}:
        return b"\x00\x00\x01\xBA" + (b"\x00" * 12)
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"


def _build_synthetic_probe_response(*, content_name, content_obj, relation, range_header):
    total_size = _estimate_probe_content_length(content_obj, relation)
    extension = _get_probe_container_extension(relation)
    content_type_header = (
        infer_content_type_from_url(f"http://dispatcharr.invalid/probe.{extension}")
        or "video/mp4"
    )

    start = 0
    if range_header:
        try:
            range_spec = str(range_header).strip().lower()[len("bytes="):]
            start_part, _ = range_spec.split("-", 1)
            start = int(start_part) if start_part.strip() else 0
        except (ValueError, TypeError):
            start = 0

    if start >= total_size:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{total_size}"
        response["Accept-Ranges"] = "bytes"
        response["X-Dispatcharr-Probe-Mode"] = "1"
        response["X-Dispatcharr-Probe-Synthetic"] = "1"
        return response

    body_length = min(2048, total_size - start)
    header_bytes = _get_synthetic_probe_header_bytes(extension)

    if start < len(header_bytes):
        body = header_bytes[start:start + body_length]
        if len(body) < body_length:
            body += b"\x00" * (body_length - len(body))
    else:
        body = b"\x00" * body_length

    end = start + body_length - 1
    response = HttpResponse(body, status=206, content_type=content_type_header)
    response["Cache-Control"] = "no-cache"
    response["Pragma"] = "no-cache"
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(body_length)
    response["Content-Range"] = f"bytes {start}-{end}/{total_size}"
    response["X-Dispatcharr-Probe-Mode"] = "1"
    response["X-Dispatcharr-Probe-Synthetic"] = "1"
    logger.info(
        "[VOD-PROBE] Returning synthetic local probe response for %s (ext=%s start=%s len=%s total=%s)",
        content_name,
        extension,
        start,
        body_length,
        total_size,
    )
    return response


def _build_synthetic_probe_head_response(*, session_url, session_id, content_obj, relation):
    total_size = _estimate_probe_content_length(content_obj, relation)
    extension = _get_probe_container_extension(relation)
    content_type_header = (
        infer_content_type_from_url(f"http://dispatcharr.invalid/probe.{extension}")
        or "video/mp4"
    )

    response = HttpResponse(status=200, content_type=content_type_header)
    response["Content-Length"] = str(total_size)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "no-cache"
    response["Pragma"] = "no-cache"
    response["X-Session-URL"] = session_url
    response["X-Dispatcharr-Session"] = session_id
    response["X-Dispatcharr-Probe-Mode"] = "1"
    response["X-Dispatcharr-Probe-Synthetic"] = "1"
    logger.info(
        "[VOD-HEAD] Returning synthetic local HEAD response (ext=%s total=%s session=%s)",
        extension,
        total_size,
        session_id,
    )
    return response


def _stream_stalker_probe_content(
    *,
    content_name,
    content_obj,
    relation,
    range_header=None,
):
    return _build_synthetic_probe_response(
        content_name=content_name,
        content_obj=content_obj,
        relation=relation,
        range_header=range_header,
    )

def _active_vod_account_filters():
    return {
        "m3u_account__is_active": True,
        "m3u_account__custom_properties__enable_vod": True,
    }


def _load_existing_vod_session_target(session_id):
    """Return the stored upstream target for an existing VOD session, if any."""
    if not session_id:
        return None

    try:
        connection = RedisBackedVODConnection(session_id)
        state = connection._get_connection_state()
    except Exception as exc:
        logger.warning(
            f"[VOD-SESSION] Failed to load existing session target for {session_id}: {exc}"
        )
        return None

    if not state or not state.stream_url:
        return None

    return {
        "url": state.stream_url,
        "user_agent": state.headers.get("User-Agent") if state.headers else None,
        "input_headers": state.headers or None,
    }


def _get_content_and_relation(content_type, content_id, preferred_m3u_account_id=None, preferred_stream_id=None):
    """Get the content object and its M3U relation"""
    try:
        logger.info(f"[CONTENT-LOOKUP] Looking up {content_type} with UUID {content_id}")
        if preferred_m3u_account_id:
            logger.info(f"[CONTENT-LOOKUP] Preferred M3U account ID: {preferred_m3u_account_id}")
        if preferred_stream_id:
            logger.info(f"[CONTENT-LOOKUP] Preferred stream ID: {preferred_stream_id}")

        if content_type == 'movie':
            content_obj = Movie.objects.filter(uuid=content_id).first()
            if content_obj is None and preferred_stream_id:
                # UUIDs are regenerated when process_movie_batch
                # (apps/vod/tasks.py) creates duplicate vod_movie records
                # during refresh — see #961 / #973. stream_id is stable
                # (unique per (m3u_account, stream_id)) so it's a safe
                # fallback for previously-cached external player URLs.
                # Strictest-match first: prefer the requested account, then
                # any active account by priority (matches the existing
                # relation-selection ordering below).
                rel = None
                if preferred_m3u_account_id:
                    rel = (
                        M3UMovieRelation.objects
                        .filter(
                            stream_id=preferred_stream_id,
                            m3u_account_id=preferred_m3u_account_id,
                            **_active_vod_account_filters(),
                        )
                        .select_related('movie', 'm3u_account')
                        .first()
                    )
                if rel is None:
                    rel = (
                        M3UMovieRelation.objects
                        .filter(
                            stream_id=preferred_stream_id,
                            **_active_vod_account_filters(),
                        )
                        .select_related('movie', 'm3u_account')
                        .order_by('-m3u_account__priority', 'id')
                        .first()
                    )
                if rel is not None:
                    content_obj = rel.movie
                    logger.warning(
                        f"[STREAMID-FALLBACK] Movie UUID {content_id} not "
                        f"found; resolved via stream_id "
                        f"{preferred_stream_id} -> movie uuid "
                        f"{content_obj.uuid} (provider: "
                        f"{rel.m3u_account.name})"
                    )
            if content_obj is None:
                raise Http404(
                    f"Movie not found by uuid {content_id} "
                    f"or stream_id {preferred_stream_id}"
                )
            logger.info(f"[CONTENT-FOUND] Movie: {content_obj.name} (ID: {content_obj.id})")

            # Filter by preferred stream ID first (most specific)
            relations_query = content_obj.m3u_relations.filter(
                **_active_vod_account_filters()
            )
            if preferred_stream_id:
                specific_relation = relations_query.filter(stream_id=preferred_stream_id).first()
                if specific_relation:
                    logger.info(f"[STREAM-SELECTED] Using specific stream: {specific_relation.stream_id} from provider: {specific_relation.m3u_account.name}")
                    return content_obj, specific_relation
                else:
                    logger.warning(f"[STREAM-FALLBACK] Preferred stream ID {preferred_stream_id} not found, falling back to account/priority selection")

            # Filter by preferred M3U account if specified
            if preferred_m3u_account_id:
                specific_relation = relations_query.filter(m3u_account__id=preferred_m3u_account_id).first()
                if specific_relation:
                    logger.info(f"[PROVIDER-SELECTED] Using preferred provider: {specific_relation.m3u_account.name}")
                    return content_obj, specific_relation
                else:
                    logger.warning(f"[PROVIDER-FALLBACK] Preferred M3U account {preferred_m3u_account_id} not found, using highest priority")

            # Get the highest priority active relation (fallback or default)
            relation = relations_query.select_related('m3u_account').order_by('-m3u_account__priority', 'id').first()

            if relation:
                logger.info(f"[PROVIDER-SELECTED] Using provider: {relation.m3u_account.name} (priority: {relation.m3u_account.priority})")

            return content_obj, relation

        elif content_type == 'episode':
            content_obj = Episode.objects.filter(uuid=content_id).first()
            if content_obj is None and preferred_stream_id:
                # Same rationale as the movie branch above — episode UUIDs
                # are regenerated when process_series_batch creates
                # duplicate vod_episode records during refresh.
                rel = None
                if preferred_m3u_account_id:
                    rel = (
                        M3UEpisodeRelation.objects
                        .filter(
                            stream_id=preferred_stream_id,
                            m3u_account_id=preferred_m3u_account_id,
                            **_active_vod_account_filters(),
                        )
                        .select_related('episode', 'm3u_account')
                        .first()
                    )
                if rel is None:
                    rel = (
                        M3UEpisodeRelation.objects
                        .filter(
                            stream_id=preferred_stream_id,
                            **_active_vod_account_filters(),
                        )
                        .select_related('episode', 'm3u_account')
                        .order_by('-m3u_account__priority', 'id')
                        .first()
                    )
                if rel is not None:
                    content_obj = rel.episode
                    logger.warning(
                        f"[STREAMID-FALLBACK] Episode UUID {content_id} not "
                        f"found; resolved via stream_id "
                        f"{preferred_stream_id} -> episode uuid "
                        f"{content_obj.uuid} (provider: "
                        f"{rel.m3u_account.name})"
                    )
            if content_obj is None:
                raise Http404(
                    f"Episode not found by uuid {content_id} "
                    f"or stream_id {preferred_stream_id}"
                )
            logger.info(f"[CONTENT-FOUND] Episode: {content_obj.name} (ID: {content_obj.id}, Series: {content_obj.series.name})")

            # Filter by preferred stream ID first (most specific)
            relations_query = content_obj.m3u_relations.filter(
                **_active_vod_account_filters()
            )
            if preferred_stream_id:
                specific_relation = relations_query.filter(stream_id=preferred_stream_id).first()
                if specific_relation:
                    logger.info(f"[STREAM-SELECTED] Using specific stream: {specific_relation.stream_id} from provider: {specific_relation.m3u_account.name}")
                    return content_obj, specific_relation
                else:
                    logger.warning(f"[STREAM-FALLBACK] Preferred stream ID {preferred_stream_id} not found, falling back to account/priority selection")

            # Filter by preferred M3U account if specified
            if preferred_m3u_account_id:
                specific_relation = relations_query.filter(m3u_account__id=preferred_m3u_account_id).first()
                if specific_relation:
                    logger.info(f"[PROVIDER-SELECTED] Using preferred provider: {specific_relation.m3u_account.name}")
                    return content_obj, specific_relation
                else:
                    logger.warning(f"[PROVIDER-FALLBACK] Preferred M3U account {preferred_m3u_account_id} not found, using highest priority")

            # Get the highest priority active relation (fallback or default)
            relation = relations_query.select_related('m3u_account').order_by('-m3u_account__priority', 'id').first()

            if relation:
                logger.info(f"[PROVIDER-SELECTED] Using provider: {relation.m3u_account.name} (priority: {relation.m3u_account.priority})")

            return content_obj, relation

        elif content_type == 'series':
            # For series, get the first episode
            series = get_object_or_404(Series, uuid=content_id)
            logger.info(f"[CONTENT-FOUND] Series: {series.name} (ID: {series.id})")
            episode = series.episodes.first()
            if not episode:
                logger.error(f"[CONTENT-ERROR] No episodes found for series {series.name}")
                return None, None

            logger.info(f"[CONTENT-FOUND] First episode: {episode.name} (ID: {episode.id})")

            # Filter by preferred stream ID first (most specific)
            relations_query = episode.m3u_relations.filter(
                **_active_vod_account_filters()
            )
            if preferred_stream_id:
                specific_relation = relations_query.filter(stream_id=preferred_stream_id).first()
                if specific_relation:
                    logger.info(f"[STREAM-SELECTED] Using specific stream: {specific_relation.stream_id} from provider: {specific_relation.m3u_account.name}")
                    return episode, specific_relation
                else:
                    logger.warning(f"[STREAM-FALLBACK] Preferred stream ID {preferred_stream_id} not found, falling back to account/priority selection")

            # Filter by preferred M3U account if specified
            if preferred_m3u_account_id:
                specific_relation = relations_query.filter(m3u_account__id=preferred_m3u_account_id).first()
                if specific_relation:
                    logger.info(f"[PROVIDER-SELECTED] Using preferred provider: {specific_relation.m3u_account.name}")
                    return episode, specific_relation
                else:
                    logger.warning(f"[PROVIDER-FALLBACK] Preferred M3U account {preferred_m3u_account_id} not found, using highest priority")

            # Get the highest priority active relation (fallback or default)
            relation = relations_query.select_related('m3u_account').order_by('-m3u_account__priority', 'id').first()

            if relation:
                logger.info(f"[PROVIDER-SELECTED] Using provider: {relation.m3u_account.name} (priority: {relation.m3u_account.priority})")

            return episode, relation
        else:
            logger.error(f"[CONTENT-ERROR] Invalid content type: {content_type}")
            return None, None

    except Exception as e:
        logger.error(f"Error getting content object: {e}")
        return None, None

def _get_stream_context_from_relation(relation):
    """Resolve stream URL and any provider-specific request headers."""
    started_at = time.monotonic()
    try:
        # Log the relation type and available attributes
        logger.info(f"[VOD-URL] Relation type: {type(relation).__name__}")
        logger.info(f"[VOD-URL] Account type: {relation.m3u_account.account_type}")
        logger.info(f"[VOD-URL] Stream ID: {getattr(relation, 'stream_id', 'N/A')}")

        stream_context = resolve_vod_stream_context(relation)
        if stream_context.url:
            logger.info(f"[VOD-URL] Resolved provider-aware URL: {stream_context.url}")
            logger.info(
                "[VOD-URL] Resolved stream context in %.3fs for account=%s stream_id=%s host=%s",
                time.monotonic() - started_at,
                getattr(relation.m3u_account, "id", None),
                getattr(relation, "stream_id", None),
                urlparse(stream_context.url).netloc or "unknown",
            )
            return {
                "url": stream_context.url,
                "user_agent": stream_context.user_agent,
                "input_headers": stream_context.input_headers,
            }

        # Fallback to legacy relation behavior for unsupported relation types.
        if hasattr(relation, 'get_stream_url'):
            url = relation.get_stream_url()
            if url:
                logger.info(f"[VOD-URL] Built URL from legacy get_stream_url(): {url}")
                logger.info(
                    "[VOD-URL] Built legacy stream context in %.3fs for account=%s stream_id=%s host=%s",
                    time.monotonic() - started_at,
                    getattr(relation.m3u_account, "id", None),
                    getattr(relation, "stream_id", None),
                    urlparse(url).netloc or "unknown",
                )
                return {
                    "url": url,
                    "user_agent": None,
                    "input_headers": None,
                }
            logger.warning(f"[VOD-URL] get_stream_url() returned None")

        logger.error(f"[VOD-URL] Relation has no get_stream_url method or it failed")
        return {
            "url": None,
            "user_agent": None,
            "input_headers": None,
        }
    except Exception as e:
        logger.error(
            "[VOD-URL] Error getting stream URL from relation after %.3fs: %s",
            time.monotonic() - started_at,
            e,
            exc_info=True,
        )
        return {
            "url": None,
            "user_agent": None,
            "input_headers": None,
        }


def _get_stream_context_for_request(relation, session_id=None):
    """Prefer a reusable session target before resolving a fresh provider URL."""
    existing_target = _load_existing_vod_session_target(session_id)
    if existing_target:
        logger.info(
            f"[VOD-SESSION] Reusing stored upstream target for session {session_id}"
        )
        return existing_target

    return _get_stream_context_from_relation(relation)

def _get_m3u_profile(m3u_account, profile_id, session_id=None):
    """Get appropriate M3U profile for streaming using Redis-based viewer counts

    Args:
        m3u_account: M3UAccount instance
        profile_id: Optional specific profile ID requested
        session_id: Optional session ID to check for existing connections

    Returns:
        tuple: (M3UAccountProfile, current_connections) or None if no profile found
    """
    try:
        from core.utils import RedisClient
        redis_client = RedisClient.get_client()

        if not redis_client:
            logger.warning("Redis not available, falling back to default profile")
            default_profile = M3UAccountProfile.objects.filter(
                m3u_account=m3u_account,
                is_active=True,
                is_default=True
            ).first()
            return (default_profile, 0) if default_profile else None

        # Check if this session already has an active connection
        if session_id:
            persistent_connection_key = f"vod_persistent_connection:{session_id}"
            connection_data = redis_client.hgetall(persistent_connection_key)

            if connection_data:
                existing_profile_id = connection_data.get('m3u_profile_id')
                if existing_profile_id:
                    try:
                        existing_profile = M3UAccountProfile.objects.get(
                            id=int(existing_profile_id),
                            m3u_account=m3u_account,
                            is_active=True
                        )
                        # Get current connections for logging
                        profile_connections_key = f"profile_connections:{existing_profile.id}"
                        current_connections = int(redis_client.get(profile_connections_key) or 0)

                        logger.info(f"[PROFILE-SELECTION] Session {session_id} reusing existing profile {existing_profile.id}: {current_connections}/{existing_profile.max_streams} connections")
                        return (existing_profile, current_connections)
                    except (M3UAccountProfile.DoesNotExist, ValueError):
                        logger.warning(f"[PROFILE-SELECTION] Session {session_id} has invalid profile ID {existing_profile_id}, selecting new profile")
                    except Exception as e:
                        logger.warning(f"[PROFILE-SELECTION] Error checking existing profile for session {session_id}: {e}")
                else:
                    logger.debug(f"[PROFILE-SELECTION] Session {session_id} exists but has no profile ID stored")            # If specific profile requested, try to use it
        if profile_id:
            try:
                profile = M3UAccountProfile.objects.get(
                    id=profile_id,
                    m3u_account=m3u_account,
                    is_active=True
                )
                # Check Redis-based current connections
                profile_connections_key = f"profile_connections:{profile.id}"
                current_connections = int(redis_client.get(profile_connections_key) or 0)

                if profile.max_streams == 0 or current_connections < profile.max_streams:
                    logger.info(f"[PROFILE-SELECTION] Using requested profile {profile.id}: {current_connections}/{profile.max_streams} connections")
                    return (profile, current_connections)
                else:
                    logger.warning(f"[PROFILE-SELECTION] Requested profile {profile.id} is at capacity: {current_connections}/{profile.max_streams}")
            except M3UAccountProfile.DoesNotExist:
                logger.warning(f"[PROFILE-SELECTION] Requested profile {profile_id} not found")

        # Get active profiles ordered by priority (default first)
        m3u_profiles = M3UAccountProfile.objects.filter(
            m3u_account=m3u_account,
            is_active=True
        )

        default_profile = m3u_profiles.filter(is_default=True).first()
        if not default_profile:
            logger.error(f"[PROFILE-SELECTION] No default profile found for M3U account {m3u_account.id}")
            return None

        # Check profiles in order: default first, then others
        profiles = [default_profile] + list(m3u_profiles.filter(is_default=False))

        for profile in profiles:
            profile_connections_key = f"profile_connections:{profile.id}"
            current_connections = int(redis_client.get(profile_connections_key) or 0)

            # Check if profile has available connection slots
            if profile.max_streams == 0 or current_connections < profile.max_streams:
                logger.info(f"[PROFILE-SELECTION] Selected profile {profile.id} ({profile.name}): {current_connections}/{profile.max_streams} connections")
                return (profile, current_connections)
            else:
                logger.debug(f"[PROFILE-SELECTION] Profile {profile.id} at capacity: {current_connections}/{profile.max_streams}")

        # All profiles are at capacity - return None to trigger error response
        logger.error(f"[PROFILE-SELECTION] All profiles at capacity for M3U account {m3u_account.id}, rejecting request")
        return None

    except Exception as e:
        logger.error(f"Error getting M3U profile: {e}")
        return None

def _transform_url(original_url, m3u_profile):
    """Transform URL based on M3U profile settings"""
    try:
        import regex

        if not original_url:
            return None

        search_pattern = m3u_profile.search_pattern
        replace_pattern = m3u_profile.replace_pattern
        # Convert JS-style backreferences in replace: $<name> -> \g<name>, $1 -> \1
        safe_replace_pattern = regex.sub(r'\$<([^>]+)>', r'\\g<\1>', replace_pattern)
        safe_replace_pattern = regex.sub(r'\$(\d+)', r'\\\1', safe_replace_pattern)

        if search_pattern and replace_pattern:
            # regex module accepts JS-style (?<name>...) named groups natively
            transformed_url = regex.sub(search_pattern, safe_replace_pattern, original_url)
            return transformed_url

        return original_url

    except Exception as e:
        logger.error(f"Error transforming URL: {e}")
        return original_url

@api_view(["GET"])
@authentication_classes([JWTAuthentication, ApiKeyAuthentication, QueryParamJWTAuthentication])
@permission_classes([AllowAny])
def stream_vod(request, content_type, content_id, session_id=None, profile_id=None, user=None):
    """
    Stream VOD content (movies or series episodes) with session-based connection reuse

    Args:
        content_type: 'movie', 'series', or 'episode'
        content_id: ID of the content
        session_id: Optional session ID from URL path (for persistent connections)
        profile_id: Optional M3U profile ID for authentication
    """
    if not network_access_allowed(request, "STREAMS"):
        return JsonResponse({"error": "Forbidden"}, status=403)
    if user is None and hasattr(request, "user") and request.user.is_authenticated:
        user = request.user
    logger.info(f"[VOD-REQUEST] Starting VOD stream request: {content_type}/{content_id}, session: {session_id}, profile: {profile_id}")
    logger.info(f"[VOD-REQUEST] Full request path: {request.get_full_path()}")
    logger.info(f"[VOD-REQUEST] Request method: {request.method}")
    logger.info(f"[VOD-REQUEST] Request headers: {dict(request.headers)}")

    try:
        client_ip, client_user_agent = get_client_info(request)

        # Extract timeshift parameters from query string
        # Support multiple timeshift parameter formats
        utc_start = request.GET.get('utc_start') or request.GET.get('start') or request.GET.get('playliststart')
        utc_end = request.GET.get('utc_end') or request.GET.get('end') or request.GET.get('playlistend')
        offset = request.GET.get('offset') or request.GET.get('seek') or request.GET.get('t')

        # VLC specific timeshift parameters
        if not utc_start and not offset:
            # Check for VLC-style timestamp parameters
            if 'timestamp' in request.GET:
                offset = request.GET.get('timestamp')
            elif 'time' in request.GET:
                offset = request.GET.get('time')

        # Session ID now comes from URL path parameter
        # Remove legacy query parameter extraction since we're using path-based routing

        # Extract Range header for seeking support
        range_header = request.META.get('HTTP_RANGE')

        logger.info(f"[VOD-TIMESHIFT] Timeshift params - utc_start: {utc_start}, utc_end: {utc_end}, offset: {offset}")
        logger.info(f"[VOD-SESSION] Session ID: {session_id}")

        # Log all query parameters for debugging
        if request.GET:
            logger.debug(f"[VOD-PARAMS] All query params: {dict(request.GET)}")

        if range_header:
            logger.info(f"[VOD-RANGE] Range header: {range_header}")

            # Parse the range to understand what position VLC is seeking to
            try:
                if 'bytes=' in range_header:
                    range_part = range_header.replace('bytes=', '')
                    if '-' in range_part:
                        start_byte, end_byte = range_part.split('-', 1)
                        if start_byte:
                            start_pos_mb = int(start_byte) / (1024 * 1024)
                            logger.info(f"[VOD-SEEK] Seeking to byte position: {start_byte} (~{start_pos_mb:.1f} MB)")
                            if int(start_byte) > 0:
                                logger.info(f"[VOD-SEEK] *** ACTUAL SEEK DETECTED *** Position: {start_pos_mb:.1f} MB")
                        else:
                            logger.info("[VOD-SEEK] Open-ended range request (from start)")
                        if end_byte:
                            end_pos_mb = int(end_byte) / (1024 * 1024)
                            logger.info(f"[VOD-SEEK] End position: {end_byte} bytes (~{end_pos_mb:.1f} MB)")
            except Exception as e:
                logger.warning(f"[VOD-SEEK] Could not parse range header: {e}")

            # Simple seek detection - track rapid requests
            current_time = time.time()
            request_key = f"{client_ip}:{content_type}:{content_id}"

            if request_key in _request_times:
                time_diff = current_time - _request_times[request_key]
                if time_diff < 5.0:
                    logger.info(f"[VOD-SEEK] Rapid request detected ({time_diff:.1f}s) - likely seeking")

            _request_times[request_key] = current_time
        else:
            logger.info(f"[VOD-RANGE] No Range header - full content request")

        logger.info(
            f"[VOD-CLIENT] Client info - IP: {client_ip}, "
            f"User-Agent: {(client_user_agent[:50] if client_user_agent else 'None')}..."
        )

        # Extract preferred M3U account ID and stream ID from query parameters
        preferred_m3u_account_id = request.GET.get('m3u_account_id')
        preferred_stream_id = request.GET.get('stream_id')

        if preferred_m3u_account_id:
            try:
                preferred_m3u_account_id = int(preferred_m3u_account_id)
            except (ValueError, TypeError):
                logger.warning(f"[VOD-PARAM] Invalid m3u_account_id parameter: {preferred_m3u_account_id}")
                preferred_m3u_account_id = None

        if preferred_stream_id:
            logger.info(f"[VOD-PARAM] Preferred stream ID: {preferred_stream_id}")

        # Get the content object and its relation
        content_obj, relation = _get_content_and_relation(content_type, content_id, preferred_m3u_account_id, preferred_stream_id)
        if not content_obj or not relation:
            logger.error(f"[VOD-ERROR] Content or relation not found: {content_type} {content_id}")
            raise Http404(f"Content not found: {content_type} {content_id}")

        logger.info(f"[VOD-CONTENT] Found content: {getattr(content_obj, 'name', 'Unknown')}")

        # Get M3U account from relation
        m3u_account = relation.m3u_account
        logger.info(f"[VOD-ACCOUNT] Using M3U account: {m3u_account.name}")
        probe_evaluation = (
            _evaluate_probe_mode(
                client_ip=client_ip,
                client_user_agent=client_user_agent,
                content_type=content_type,
                content_id=content_id,
                range_header=range_header,
                session_id=session_id,
                offset=offset,
                utc_start=utc_start,
                utc_end=utc_end,
            )
            if m3u_account.account_type == M3UAccount.Types.STALKER
            else {
                "enabled": False,
                "reason": "non-stalker-provider",
                "backend": None,
                "unique_content_count": None,
            }
        )
        probe_mode = (
            m3u_account.account_type == M3UAccount.Types.STALKER
            and probe_evaluation["enabled"]
        )
        logger.info(
            "[VOD-PROBE] Probe mode enabled=%s reason=%s unique_count=%s threshold=%s backend=%s session_id=%s range=%s",
            probe_mode,
            probe_evaluation["reason"],
            probe_evaluation["unique_content_count"],
            PROBE_ACTIVITY_MIN_UNIQUE_CONTENT,
            probe_evaluation["backend"],
            bool(session_id),
            range_header or "none",
        )

        # If no session ID, create one and redirect to path-based URL unless this
        # looks like a scan/probe burst that can stay on the lightweight path.
        if not session_id and not probe_mode:
            new_session_id = f"vod_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
            logger.info(f"[VOD-SESSION] Creating new session: {new_session_id}")

            # Preserve any query parameters (except session_id and token)
            query_params = dict(request.GET)
            query_params.pop('session_id', None)
            query_params.pop('token', None)  # Token not needed after session is established

            # The VOD proxy URL patterns accept session_id in the path, so we redirect
            # to a path-based URL. XC endpoints (/movie/<user>/<pass>/<id>.<ext>) have
            # a fixed shape and instead read session_id from a query parameter.
            is_vod_proxy_path = request.path.startswith('/proxy/vod/')

            if is_vod_proxy_path:
                path_parts = request.path.rstrip('/').split('/')
                if profile_id:
                    new_path = f"{'/'.join(path_parts)}/{new_session_id}/{profile_id}/"
                else:
                    new_path = f"{'/'.join(path_parts)}/{new_session_id}"

                if query_params:
                    query_string = urlencode(query_params, doseq=True)
                    redirect_url = f"{new_path}?{query_string}"
                else:
                    redirect_url = new_path
            else:
                # XC path: keep the original path, put session_id in the query string
                query_params['session_id'] = new_session_id
                query_string = urlencode(query_params, doseq=True)
                redirect_url = f"{request.path}?{query_string}"

            logger.info(f"[VOD-SESSION] Redirecting to path-based URL: {redirect_url}")

            # Persist the authenticated user to Redis so the streaming request
            # (which arrives without the token after the redirect) can resolve it.
            if user:
                try:
                    from core.utils import RedisClient
                    _r = RedisClient.get_client()
                    if _r:
                        _r.set(f"vod_session_user:{new_session_id}", user.id, ex=300)
                except Exception:
                    pass

            return HttpResponse(
                status=301,
                headers={'Location': redirect_url}
            )

        # Resolve user from Redis session mapping when the streaming request
        # arrives without auth credentials (token was stripped from redirect URL).
        # Only needed on the first streaming request - skip if connection already exists.
        if user is None and session_id:
            try:
                from core.utils import RedisClient
                _r = RedisClient.get_client()
                if _r and not _r.exists(f"vod_persistent_connection:{session_id}"):
                    stored_uid = _r.get(f"vod_session_user:{session_id}")
                    if stored_uid:
                        user = User.objects.filter(id=int(stored_uid)).first()
            except Exception:
                pass

        if user and not probe_mode:
            if not check_user_stream_limits(user, session_id, media_id=content_id):
                return JsonResponse(
                    {"error": f"Stream limit exceeded ({user.stream_limit} concurrent streams allowed)"},
                    status=429
                )

        if probe_mode:
            return _stream_stalker_probe_content(
                content_name=getattr(content_obj, 'name', 'Unknown'),
                content_obj=content_obj,
                relation=relation,
                range_header=range_header,
            )

        # Resolve provider-specific playback context before profile transforms.
        stream_context = _get_stream_context_for_request(relation, session_id=session_id)
        stream_url = stream_context.get("url")
        logger.info(f"[VOD-CONTENT] Content URL: {stream_url or 'No URL found'}")

        if not stream_url:
            logger.error(f"[VOD-ERROR] No stream URL available for {content_type} {content_id}")
            return HttpResponse("No stream URL available", status=503)

        # Get M3U profile (returns profile and current connection count)
        profile_result = _get_m3u_profile(m3u_account, profile_id, session_id)

        if not profile_result or not profile_result[0]:
            logger.error(f"[VOD-ERROR] No suitable M3U profile found for {content_type} {content_id}")
            return HttpResponse("No available stream", status=503)

        m3u_profile, current_connections = profile_result
        logger.info(f"[VOD-PROFILE] Using M3U profile: {m3u_profile.id} (max_streams: {m3u_profile.max_streams}, current: {current_connections})")

        # Connection tracking is handled by the connection manager
        # Transform URL based on profile
        final_stream_url = _transform_url(stream_url, m3u_profile)
        logger.info(f"[VOD-URL] Final stream URL: {final_stream_url}")

        # Validate stream URL
        if not final_stream_url or not final_stream_url.startswith(('http://', 'https://')):
            logger.error(f"[VOD-ERROR] Invalid stream URL: {final_stream_url}")
            return HttpResponse("Invalid stream URL", status=500)

        # Get connection manager (Redis-backed for multi-worker support)
        connection_manager = MultiWorkerVODConnectionManager.get_instance()

        # Stream the content with session-based connection reuse
        logger.info("[VOD-STREAM] Calling connection manager to stream content")
        response = connection_manager.stream_content_with_session(
            session_id=session_id,
            content_obj=content_obj,
            stream_url=final_stream_url,
            m3u_profile=m3u_profile,
            client_ip=client_ip,
            client_user_agent=client_user_agent,
            request=request,
            utc_start=utc_start,
            utc_end=utc_end,
            offset=offset,
            range_header=range_header,
            input_headers=stream_context.get("input_headers"),
            user=user,
            relation=relation,
        )

        logger.info(f"[VOD-SUCCESS] Stream response created successfully, type: {type(response)}")
        return response

    except Exception as e:
        logger.error(f"[VOD-EXCEPTION] Error streaming {content_type} {content_id}: {e}", exc_info=True)
        return HttpResponse(f"Streaming error: {str(e)}", status=500)

@api_view(["HEAD"])
@authentication_classes([JWTAuthentication, ApiKeyAuthentication, QueryParamJWTAuthentication])
@permission_classes([AllowAny])
def head_vod(request, content_type, content_id, session_id=None, profile_id=None):
    """
    Handle HEAD requests for FUSE filesystem integration

    Returns content length and session URL header for subsequent GET requests
    """
    if not network_access_allowed(request, "STREAMS"):
        return JsonResponse({"error": "Forbidden"}, status=403)

    logger.info(f"[VOD-HEAD] HEAD request: {content_type}/{content_id}, session: {session_id}, profile: {profile_id}")

    try:
        # Get client info for M3U profile selection
        client_ip, client_user_agent = get_client_info(request)
        logger.info(f"[VOD-HEAD] Client info - IP: {client_ip}, User-Agent: {client_user_agent[:50] if client_user_agent else 'None'}...")

        # If no session ID, create one (same logic as GET)
        if not session_id:
            new_session_id = f"vod_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
            logger.info(f"[VOD-HEAD] Creating new session for HEAD: {new_session_id}")

            # Build session URL for response header
            path_parts = request.path.rstrip('/').split('/')
            if profile_id:
                session_url = f"{'/'.join(path_parts)}/{new_session_id}/{profile_id}/"
            else:
                session_url = f"{'/'.join(path_parts)}/{new_session_id}"

            session_id = new_session_id
        else:
            # Session already in URL, construct the current session URL
            session_url = request.path
            logger.info(f"[VOD-HEAD] Using existing session: {session_id}")

        # Extract preferred M3U account ID and stream ID from query parameters
        preferred_m3u_account_id = request.GET.get('m3u_account_id')
        preferred_stream_id = request.GET.get('stream_id')

        if preferred_m3u_account_id:
            try:
                preferred_m3u_account_id = int(preferred_m3u_account_id)
            except (ValueError, TypeError):
                logger.warning(f"[VOD-HEAD] Invalid m3u_account_id parameter: {preferred_m3u_account_id}")
                preferred_m3u_account_id = None

        if preferred_stream_id:
            logger.info(f"[VOD-HEAD] Preferred stream ID: {preferred_stream_id}")

        # Get content and relation (same as GET)
        content_obj, relation = _get_content_and_relation(content_type, content_id, preferred_m3u_account_id, preferred_stream_id)
        if not content_obj or not relation:
            logger.error(f"[VOD-HEAD] Content or relation not found: {content_type} {content_id}")
            return HttpResponse("Content not found", status=404)

        head_probe_evaluation = (
            _evaluate_probe_mode(
                client_ip=client_ip,
                client_user_agent=client_user_agent,
                content_type=content_type,
                content_id=content_id,
                range_header=None,
                session_id=session_id,
                offset=None,
                utc_start=None,
                utc_end=None,
            )
            if relation.m3u_account.account_type == M3UAccount.Types.STALKER
            else {
                "enabled": False,
                "reason": "non-stalker-provider",
                "backend": None,
                "unique_content_count": None,
            }
        )
        head_probe_mode = (
            relation.m3u_account.account_type == M3UAccount.Types.STALKER
            and head_probe_evaluation["enabled"]
        )
        logger.info(
            "[VOD-HEAD-PROBE] Probe mode enabled=%s reason=%s unique_count=%s threshold=%s backend=%s session_id=%s",
            head_probe_mode,
            head_probe_evaluation["reason"],
            head_probe_evaluation["unique_content_count"],
            PROBE_ACTIVITY_MIN_UNIQUE_CONTENT,
            head_probe_evaluation["backend"],
            bool(session_id),
        )

        if head_probe_mode:
            return _build_synthetic_probe_head_response(
                session_url=session_url,
                session_id=session_id,
                content_obj=content_obj,
                relation=relation,
            )

        # Get M3U account and stream URL
        m3u_account = relation.m3u_account
        stream_context = _get_stream_context_for_request(relation, session_id=session_id)
        stream_url = stream_context.get("url")
        if not stream_url:
            logger.error(f"[VOD-HEAD] No stream URL available for {content_type} {content_id}")
            return HttpResponse("No stream URL available", status=503)

        # Get M3U profile (returns profile and current connection count)
        profile_result = _get_m3u_profile(m3u_account, profile_id, session_id)
        if not profile_result or not profile_result[0]:
            logger.error(f"[VOD-HEAD] No M3U profile found or all profiles at capacity")
            return HttpResponse("No available stream", status=503)

        m3u_profile, current_connections = profile_result

        # Transform URL if needed
        final_stream_url = _transform_url(stream_url, m3u_profile)

        # Make a small range GET request to get content length since providers don't support HEAD
        # We'll use a tiny range to minimize data transfer but get the headers we need
        # Use M3U account's user agent as primary, client user agent as fallback
        m3u_user_agent = m3u_account.get_user_agent().user_agent if m3u_account.get_user_agent() else None
        headers = {
            'User-Agent': m3u_user_agent or client_user_agent or 'Dispatcharr/1.0',
            'Accept': '*/*',
            'Range': 'bytes=0-1'  # Request only first 2 bytes
        }
        if stream_context.get("input_headers"):
            headers.update(stream_context["input_headers"])

        logger.info(f"[VOD-HEAD] Making small range GET request to provider: {final_stream_url}")
        response = requests.get(final_stream_url, headers=headers, timeout=30, allow_redirects=True, stream=True)

        # Check for range support - should be 206 for partial content
        if response.status_code == 206:
            # Parse Content-Range header to get total file size
            content_range = response.headers.get('Content-Range', '')
            if content_range:
                # Content-Range: bytes 0-1/1234567890
                total_size = content_range.split('/')[-1]
                logger.info(f"[VOD-HEAD] Got file size from Content-Range: {total_size}")
            else:
                logger.warning(f"[VOD-HEAD] No Content-Range header in 206 response")
                total_size = response.headers.get('Content-Length', '0')
        elif response.status_code == 200:
            # Server doesn't support range requests, use Content-Length from full response
            total_size = response.headers.get('Content-Length', '0')
            logger.info(f"[VOD-HEAD] Server doesn't support ranges, got Content-Length: {total_size}")
        else:
            logger.error(f"[VOD-HEAD] Provider GET request failed: {response.status_code}")
            return HttpResponse("Provider error", status=response.status_code)

        # Close the small range request - we don't need to keep this connection
        response.close()

        # Store the total content length in Redis for the persistent connection to use
        try:
            import redis
            from django.conf import settings
            redis_host = getattr(settings, 'REDIS_HOST', 'localhost')
            redis_port = int(getattr(settings, 'REDIS_PORT', 6379))
            redis_db = int(getattr(settings, 'REDIS_DB', 0))
            redis_password = getattr(settings, 'REDIS_PASSWORD', '')
            redis_user = getattr(settings, 'REDIS_USER', '')
            ssl_params = getattr(settings, 'REDIS_SSL_PARAMS', {})
            r = redis.StrictRedis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password if redis_password else None,
                username=redis_user if redis_user else None,
                decode_responses=True,
                **ssl_params
            )
            content_length_key = f"vod_content_length:{session_id}"
            r.set(content_length_key, total_size, ex=1800)  # Store for 30 minutes
            logger.info(f"[VOD-HEAD] Stored total content length {total_size} for session {session_id}")
        except Exception as e:
            logger.error(f"[VOD-HEAD] Failed to store content length in Redis: {e}")

        # Now create a persistent connection for the session (if one doesn't exist)
        # This ensures the FUSE GET requests will reuse the same connection

        connection_manager = MultiWorkerVODConnectionManager.get_instance()

        logger.info(f"[VOD-HEAD] Pre-creating persistent connection for session: {session_id}")

        # We don't actually stream content here, just ensure connection is ready
        # The actual GET requests from FUSE will use the persistent connection

        # Use the total_size we extracted from the range response
        provider_content_type = response.headers.get('Content-Type')

        if provider_content_type:
            content_type_header = provider_content_type
            logger.info(f"[VOD-HEAD] Using provider Content-Type: {content_type_header}")
        else:
            # Provider didn't send Content-Type, infer from URL
            inferred_content_type = infer_content_type_from_url(final_stream_url)
            if inferred_content_type:
                content_type_header = inferred_content_type
                logger.info(f"[VOD-HEAD] Provider missing Content-Type, inferred from URL: {content_type_header}")
            else:
                content_type_header = 'video/mp4'
                logger.info(f"[VOD-HEAD] No Content-Type from provider and could not infer from URL, using default: {content_type_header}")

        logger.info(f"[VOD-HEAD] Provider response - Total Size: {total_size}, Type: {content_type_header}")

        # Create response with content length and session URL header
        head_response = HttpResponse()
        head_response['Content-Length'] = total_size
        head_response['Content-Type'] = content_type_header
        head_response['Accept-Ranges'] = 'bytes'

        # Custom header with session URL for FUSE
        head_response['X-Session-URL'] = session_url
        head_response['X-Dispatcharr-Session'] = session_id

        logger.info(f"[VOD-HEAD] Returning HEAD response with session URL: {session_url}")
        return head_response

    except Exception as e:
        logger.error(f"[VOD-HEAD] Error in HEAD request: {e}", exc_info=True)
        return HttpResponse(f"HEAD error: {str(e)}", status=500)


class VODStreamView:
    """Compatibility wrapper for tests that still exercise the old class API."""

    _get_content_and_relation = staticmethod(_get_content_and_relation)
    _get_m3u_profile = staticmethod(_get_m3u_profile)
    _get_stream_context_for_request = staticmethod(_get_stream_context_for_request)

    def _call_with_overrides(self, func, request, content_type, content_id, session_id=None, profile_id=None):
        original_content_lookup = globals()["_get_content_and_relation"]
        original_profile_lookup = globals()["_get_m3u_profile"]
        original_stream_context_lookup = globals()["_get_stream_context_for_request"]
        globals()["_get_content_and_relation"] = self._get_content_and_relation
        globals()["_get_m3u_profile"] = self._get_m3u_profile
        globals()["_get_stream_context_for_request"] = self._get_stream_context_for_request
        try:
            return func(request, content_type, content_id, session_id, profile_id)
        finally:
            globals()["_get_content_and_relation"] = original_content_lookup
            globals()["_get_m3u_profile"] = original_profile_lookup
            globals()["_get_stream_context_for_request"] = original_stream_context_lookup

    def get(self, request, content_type, content_id, session_id=None, profile_id=None):
        return self._call_with_overrides(
            stream_vod,
            request,
            content_type,
            content_id,
            session_id=session_id,
            profile_id=profile_id,
        )

    def head(self, request, content_type, content_id, session_id=None, profile_id=None):
        return self._call_with_overrides(
            head_vod,
            request,
            content_type,
            content_id,
            session_id=session_id,
            profile_id=profile_id,
        )

def build_vod_stats_data(redis_client):
    """
    Build the full VOD stats payload (with DB lookups) from Redis connection data.
    Returns a dict: {'vod_connections': [...], 'total_connections': N, 'timestamp': T}
    Used by both the vod_stats API view and the WebSocket push in _do_vod_stats_update.
    """
    try:
        # Get all VOD persistent connections (consolidated data)
        pattern = "vod_persistent_connection:*"
        cursor = 0
        connections = []
        current_time = time.time()

        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)

            for key in keys:
                try:
                    connection_data = redis_client.hgetall(key)

                    if connection_data:
                        # Extract session ID from key
                        session_id = key.replace('vod_persistent_connection:', '')

                        # Decode Redis hash data
                        combined_data = {}
                        for k, v in connection_data.items():
                            combined_data[k] = v

                        # Get content info from the connection data (using correct field names)
                        content_type = combined_data.get('content_obj_type', 'unknown')
                        content_uuid = combined_data.get('content_uuid', 'unknown')
                        client_id = session_id

                        # Get content info with enhanced metadata
                        content_name = "Unknown"
                        content_metadata = {}
                        try:
                            if content_type == 'movie':
                                content_obj = Movie.objects.select_related('logo').get(uuid=content_uuid)
                                content_name = content_obj.name

                                # Get duration from content object
                                duration_secs = None
                                if hasattr(content_obj, 'duration_secs') and content_obj.duration_secs:
                                    duration_secs = content_obj.duration_secs

                                # If we don't have duration_secs, try to calculate it from file size and position data
                                if not duration_secs:
                                    file_size_bytes = int(combined_data.get('total_content_size', 0))
                                    last_seek_byte = int(combined_data.get('last_seek_byte', 0))
                                    last_seek_percentage = float(combined_data.get('last_seek_percentage', 0.0))

                                    # Calculate position if we have the required data
                                    if file_size_bytes and file_size_bytes > 0 and last_seek_percentage > 0:
                                        # If we know the seek percentage and current time position, we can estimate duration
                                        # But we need to know the current time position in seconds first
                                        # For now, let's use a rough estimate based on file size and typical bitrates
                                        # This is a fallback - ideally duration should be in the database
                                        estimated_duration = 6000  # 100 minutes as default for movies
                                        duration_secs = estimated_duration

                                content_metadata = {
                                    'year': content_obj.year,
                                    'rating': content_obj.rating,
                                    'genre': content_obj.genre,
                                    'duration_secs': duration_secs,
                                    'description': content_obj.description,
                                    'logo_url': content_obj.logo.url if content_obj.logo else None,
                                    'tmdb_id': content_obj.tmdb_id,
                                    'imdb_id': content_obj.imdb_id
                                }
                            elif content_type == 'episode':
                                content_obj = Episode.objects.select_related('series', 'series__logo').get(uuid=content_uuid)
                                content_name = f"{content_obj.series.name} - {content_obj.name}"

                                # Get duration from content object
                                duration_secs = None
                                if hasattr(content_obj, 'duration_secs') and content_obj.duration_secs:
                                    duration_secs = content_obj.duration_secs

                                # If we don't have duration_secs, estimate for episodes
                                if not duration_secs:
                                    estimated_duration = 2400  # 40 minutes as default for episodes
                                    duration_secs = estimated_duration

                                content_metadata = {
                                    'series_name': content_obj.series.name,
                                    'episode_name': content_obj.name,
                                    'season_number': content_obj.season_number,
                                    'episode_number': content_obj.episode_number,
                                    'air_date': content_obj.air_date.isoformat() if content_obj.air_date else None,
                                    'rating': content_obj.rating,
                                    'duration_secs': duration_secs,
                                    'description': content_obj.description,
                                    'logo_url': content_obj.series.logo.url if content_obj.series.logo else None,
                                    'series_year': content_obj.series.year,
                                    'series_genre': content_obj.series.genre,
                                    'tmdb_id': content_obj.tmdb_id,
                                    'imdb_id': content_obj.imdb_id
                                }
                        except:
                            pass

                        # Get M3U profile information
                        m3u_profile_info = {}
                        m3u_profile_id = combined_data.get('m3u_profile_id')
                        if m3u_profile_id:
                            try:
                                from apps.m3u.models import M3UAccountProfile
                                profile = M3UAccountProfile.objects.select_related('m3u_account').get(id=m3u_profile_id)
                                m3u_profile_info = {
                                    'profile_name': profile.name,
                                    'account_name': profile.m3u_account.name,
                                    'account_id': profile.m3u_account.id,
                                    'max_streams': profile.m3u_account.max_streams,
                                    'm3u_profile_id': int(m3u_profile_id)
                                }
                            except Exception as e:
                                logger.warning(f"Could not fetch M3U profile {m3u_profile_id}: {e}")

                        # Also try to get profile info from stored data if database lookup fails
                        if not m3u_profile_info and combined_data.get('m3u_profile_name'):
                            m3u_profile_info = {
                                'profile_name': combined_data.get('m3u_profile_name', 'Unknown Profile'),
                                'm3u_profile_id': combined_data.get('m3u_profile_id'),
                                'account_name': 'Unknown Account'  # We don't store account name directly
                            }

                        # Calculate estimated current position based on seek percentage or last known position
                        last_known_position = int(combined_data.get('position_seconds', 0))
                        last_position_update = combined_data.get('last_position_update')
                        last_seek_percentage = float(combined_data.get('last_seek_percentage', 0.0))
                        last_seek_timestamp = float(combined_data.get('last_seek_timestamp', 0.0))
                        estimated_position = last_known_position

                        # If we have seek percentage and content duration, calculate position from that
                        if last_seek_percentage > 0 and content_metadata.get('duration_secs'):
                            try:
                                duration_secs = int(content_metadata['duration_secs'])
                                # Calculate position from seek percentage
                                seek_position = int((last_seek_percentage / 100) * duration_secs)

                                # If we have a recent seek timestamp, add elapsed time since seek
                                if last_seek_timestamp > 0:
                                    elapsed_since_seek = current_time - last_seek_timestamp
                                    # Add elapsed time but don't exceed content duration
                                    estimated_position = min(
                                        seek_position + int(elapsed_since_seek),
                                        duration_secs
                                    )
                                else:
                                    estimated_position = seek_position
                            except (ValueError, TypeError):
                                pass
                        elif last_position_update and content_metadata.get('duration_secs'):
                            # Fallback: use time-based estimation from position_seconds
                            try:
                                update_timestamp = float(last_position_update)
                                elapsed_since_update = current_time - update_timestamp
                                # Add elapsed time to last known position, but don't exceed content duration
                                estimated_position = min(
                                    last_known_position + int(elapsed_since_update),
                                    int(content_metadata['duration_secs'])
                                )
                            except (ValueError, TypeError):
                                # If timestamp parsing fails, fall back to last known position
                                estimated_position = last_known_position

                        connection_info = {
                            'content_type': content_type,
                            'content_uuid': content_uuid,
                            'content_name': content_name,
                            'content_metadata': content_metadata,
                            'm3u_profile': m3u_profile_info,
                            'client_id': client_id,
                            'client_ip': combined_data.get('client_ip', 'Unknown'),
                            'user_id': combined_data.get('user_id', '0'),
                            'user_agent': combined_data.get('client_user_agent', 'Unknown'),
                            'connected_at': combined_data.get('created_at'),
                            'last_activity': combined_data.get('last_activity'),
                            'm3u_profile_id': m3u_profile_id,
                            'position_seconds': estimated_position,  # Use estimated position
                            'last_known_position': last_known_position,  # Include raw position for debugging
                            'last_position_update': last_position_update,  # Include timestamp for frontend use
                            'bytes_sent': int(combined_data.get('bytes_sent', 0)),
                            # Seek/range information for position calculation and frontend display
                            'last_seek_byte': int(combined_data.get('last_seek_byte', 0)),
                            'last_seek_percentage': float(combined_data.get('last_seek_percentage', 0.0)),
                            'total_content_size': int(combined_data.get('total_content_size', 0)),
                            'last_seek_timestamp': float(combined_data.get('last_seek_timestamp', 0.0))
                        }

                        # Calculate connection duration
                        duration_calculated = False
                        if connection_info['connected_at']:
                            try:
                                connected_time = float(connection_info['connected_at'])
                                duration = current_time - connected_time
                                connection_info['duration'] = int(duration)
                                duration_calculated = True
                            except:
                                pass

                        # Fallback: use last_activity if connected_at is not available
                        if not duration_calculated and connection_info['last_activity']:
                            try:
                                last_activity_time = float(connection_info['last_activity'])
                                # Estimate connection duration using client_id timestamp if available
                                if connection_info['client_id'].startswith('vod_'):
                                    # Extract timestamp from client_id (format: vod_timestamp_random)
                                    parts = connection_info['client_id'].split('_')
                                    if len(parts) >= 2:
                                        client_start_time = float(parts[1]) / 1000.0  # Convert ms to seconds
                                        duration = current_time - client_start_time
                                        connection_info['duration'] = int(duration)
                                        duration_calculated = True
                            except:
                                pass

                        # Final fallback
                        if not duration_calculated:
                            connection_info['duration'] = 0

                        connections.append(connection_info)

                except Exception as e:
                    logger.error(f"Error processing connection key {key}: {e}")

            if cursor == 0:
                break

        # Group connections by content
        content_stats = {}
        for conn in connections:
            content_key = f"{conn['content_type']}:{conn['content_uuid']}"
            if content_key not in content_stats:
                content_stats[content_key] = {
                    'content_type': conn['content_type'],
                    'content_name': conn['content_name'],
                    'content_uuid': conn['content_uuid'],
                    'content_metadata': conn['content_metadata'],
                    'connection_count': 0,
                    'connections': []
                }
            content_stats[content_key]['connection_count'] += 1
            content_stats[content_key]['connections'].append(conn)

        return {
            'vod_connections': list(content_stats.values()),
            'total_connections': len(connections),
            'timestamp': current_time
        }

    except Exception as e:
        logger.error(f"Error building VOD stats: {e}")
        return {'vod_connections': [], 'total_connections': 0, 'timestamp': time.time()}


@api_view(["GET"])
@permission_classes([IsAdmin])
def vod_stats(request):
    """Get current VOD connection statistics"""
    try:
        connection_manager = MultiWorkerVODConnectionManager.get_instance()
        redis_client = connection_manager.redis_client

        if not redis_client:
            return JsonResponse({'error': 'Redis not available'}, status=500)

        return JsonResponse(build_vod_stats_data(redis_client))

    except Exception as e:
        logger.error(f"Error getting VOD stats: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@api_view(["POST"])
@permission_classes([IsAdmin])
def stop_vod_client(request):
    """Stop a specific VOD client connection using stop signal mechanism"""
    try:
        # Parse request body
        import json
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        client_id = data.get('client_id')
        if not client_id:
            return JsonResponse({'error': 'No client_id provided'}, status=400)

        logger.info(f"Request to stop VOD client: {client_id}")

        # Get Redis client
        connection_manager = MultiWorkerVODConnectionManager.get_instance()
        redis_client = connection_manager.redis_client

        if not redis_client:
            return JsonResponse({'error': 'Redis not available'}, status=500)

        # Check if connection exists
        connection_key = f"vod_persistent_connection:{client_id}"
        connection_data = redis_client.hgetall(connection_key)
        if not connection_data:
            logger.warning(f"VOD connection not found: {client_id}")
            return JsonResponse({'error': 'Connection not found'}, status=404)

        # Set a stop signal key that the worker will check
        stop_key = get_vod_client_stop_key(client_id)
        redis_client.setex(stop_key, 60, "true")  # 60 second TTL

        logger.info(f"Set stop signal for VOD client: {client_id}")

        return JsonResponse({
            'message': 'VOD client stop signal sent',
            'client_id': client_id,
            'stop_key': stop_key
        })

    except Exception as e:
        logger.error(f"Error stopping VOD client: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)

@api_view(["GET"])
@permission_classes([AllowAny])
def stream_xc_movie(request, username, password, stream_id, extension):
    if not network_access_allowed(request, "STREAMS"):
        return JsonResponse({"error": "Forbidden"}, status=403)

    from apps.vod.models import M3UMovieRelation

    session_id = request.GET.get('session_id')
    profile_id = request.GET.get('profile_id')

    user = get_object_or_404(User, username=username)

    if not network_access_allowed(request, 'STREAMS', user):
        return Response({"error": "Forbidden"}, status=403)

    custom_properties = user.custom_properties or {}

    if "xc_password" not in custom_properties:
        return Response({"error": "Invalid credentials"}, status=401)

    if custom_properties["xc_password"] != password:
        return Response({"error": "Invalid credentials"}, status=401)

    try:
        # XC movie catalogs expose M3UMovieRelation.id as the stream ID.
        # Fall back to movie_id to preserve compatibility with older cached URLs.
        movie_relation = (
            M3UMovieRelation.objects
            .select_related('movie')
            .filter(id=stream_id, **_active_vod_account_filters())
            .order_by('-m3u_account__priority', 'id')
            .first()
        )
        if movie_relation is None:
            movie_relation = (
                M3UMovieRelation.objects
                .select_related('movie')
                .filter(movie_id=stream_id, **_active_vod_account_filters())
                .order_by('-m3u_account__priority', 'id')
                .first()
            )
        if not movie_relation:
            return JsonResponse({"error": "Movie not found"}, status=404)
    except (M3UMovieRelation.DoesNotExist, M3UMovieRelation.MultipleObjectsReturned):
        return JsonResponse({"error": "Movie not found"}, status=404)

    return stream_vod(request._request, 'movie', movie_relation.movie.uuid, session_id, profile_id, user)

@api_view(["GET"])
@permission_classes([AllowAny])
def stream_xc_episode(request, username, password, stream_id, extension):
    if not network_access_allowed(request, "STREAMS"):
        return JsonResponse({"error": "Forbidden"}, status=403)

    from apps.vod.models import M3UEpisodeRelation

    session_id = request.GET.get('session_id')
    profile_id = request.GET.get('profile_id')

    user = get_object_or_404(User, username=username)

    if not network_access_allowed(request, 'STREAMS', user):
        return Response({"error": "Forbidden"}, status=403)

    custom_properties = user.custom_properties or {}

    if "xc_password" not in custom_properties:
        return Response({"error": "Invalid credentials"}, status=401)

    if custom_properties["xc_password"] != password:
        return Response({"error": "Invalid credentials"}, status=401)

    # All authenticated users get access to series/episodes from all active M3U accounts
    filters = {
        "episode_id": stream_id,
        **_active_vod_account_filters(),
    }

    try:
        episode_relation = M3UEpisodeRelation.objects.select_related('episode').filter(**filters).order_by('-m3u_account__priority', 'id').first()
    except M3UEpisodeRelation.DoesNotExist:
        return JsonResponse({"error": "Episode not found"}, status=404)

    return stream_vod(request._request, 'episode', episode_relation.episode.uuid, session_id, profile_id, user)
