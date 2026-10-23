"""Thumbnail, hover-preview, gallery and display helpers."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit, urljoin

# Default matches the example source CDN; set THUMBNAIL_BASE_URL in Vercel to override.
DEFAULT_THUMBNAIL_BASE = "https://thumb-cdn77.xvideos-cdn.com"


def _base_url(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value.rstrip("/")
    return ""


def _join_base(base: str, path: str) -> str:
    if not base:
        return path
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def thumbnail_url(raw_path: str | None) -> str:
    if not raw_path:
        return ""
    value = str(raw_path).strip()
    parsed = urlsplit(value)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return value
    base = _base_url("THUMBNAIL_BASE_URL", "IMAGE_BASE_URL") or DEFAULT_THUMBNAIL_BASE
    return _join_base(base, value)


def preview_url(raw_path: str | None) -> str:
    """Map /<asset-id>/0/xv_N_t.jpg to /<asset-id>/0/preview.mp4."""
    if not raw_path:
        return ""
    value = str(raw_path).strip()
    parsed = urlsplit(value)
    path = parsed.path if parsed.scheme in ("http", "https") and parsed.netloc else value
    replaced, count = re.subn(r"(/0/)[^/]+$", r"\1preview.mp4", path)
    if not count:
        return ""

    dedicated_base = _base_url("PREVIEW_BASE_URL")
    if dedicated_base:
        return _join_base(dedicated_base, replaced)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, replaced, "", ""))
    base = _base_url("THUMBNAIL_BASE_URL", "IMAGE_BASE_URL") or DEFAULT_THUMBNAIL_BASE
    return _join_base(base, replaced)


def mosaic_url(raw_path: str | None) -> str:
    """Map a poster path like /<asset-id>/0/xv_29_t.jpg to its mosaic sprite."""
    if not raw_path:
        return ""
    value = str(raw_path).strip()
    parsed = urlsplit(value)
    path = parsed.path if parsed.scheme in ("http", "https") and parsed.netloc else value
    replaced, count = re.subn(r"(/0/)[^/]+$", r"\1mozaique_full.jpg", path)
    if not count:
        return ""
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, replaced, "", ""))
    base = _base_url("THUMBNAIL_BASE_URL", "IMAGE_BASE_URL") or DEFAULT_THUMBNAIL_BASE
    return _join_base(base, replaced)


def image_variant_url(raw_path: str | None, number: int) -> str:
    if not raw_path or not 1 <= int(number) <= 30:
        return ""
    value = str(raw_path).strip()
    parsed = urlsplit(value)
    path = parsed.path if parsed.scheme in ("http", "https") and parsed.netloc else value
    replaced, count = re.subn(r"(/0/)[^/]+$", rf"\1xv_{int(number)}_t.jpg", path)
    if not count:
        return ""
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, replaced, "", ""))
    base = _base_url("THUMBNAIL_BASE_URL", "IMAGE_BASE_URL") or DEFAULT_THUMBNAIL_BASE
    return _join_base(base, replaced)


def format_views(value: object) -> str:
    try:
        count = max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return "—"
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B".replace(".0B", "B")
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M".replace(".0M", "M")
    if count >= 1_000:
        return f"{count / 1_000:.1f}K".replace(".0K", "K")
    return f"{count:,}"


def format_duration(seconds: object, fallback: object = None) -> str:
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError, OverflowError):
        return str(fallback or "")
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def decorate_video(video: dict) -> dict:
    item = dict(video)
    item["thumbnail_url"] = thumbnail_url(item.get("img"))
    item["preview_url"] = preview_url(item.get("img"))
    item["mosaic_url"] = mosaic_url(item.get("img"))
    item["video_href"] = f"/video/{_route_slug(item.get('title'))}--{_route_token(str(item.get('url') or ''))}"
    item["image_gallery_href"] = f"/images/{_route_slug(item.get('title'))}--{_route_token(str(item.get('url') or ''))}"
    channel = str(item.get("channel_name") or "").strip()
    item["channel_href"] = (
        f"/channel/{_route_slug(channel)}--{_route_token(channel)}" if channel else ""
    )
    raw_tags = item.get("tags") or []
    item["tags"] = list(raw_tags) if isinstance(raw_tags, (list, tuple)) else []
    try:
        percentage = float(item.get("rating_percentage")) if item.get("rating_percentage") is not None else None
    except (TypeError, ValueError):
        percentage = None
    item["rating_label"] = f"{percentage:.0f}%" if percentage is not None else ""
    item["views_label"] = format_views(item.get("views"))
    item["duration_label"] = format_duration(item.get("duration_seconds"), item.get("duration"))
    return item


def _route_slug(value: object) -> str:
    # Local import avoids coupling the media module to route registration.
    from web.ids import slugify
    return slugify(str(value or ""))


def _route_token(value: str) -> str:
    from web.ids import encode_token
    return encode_token(value)
