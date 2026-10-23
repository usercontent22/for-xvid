"""Canonical URLs and Schema.org JSON-LD helpers for public HTML pages."""
from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit

from flask import has_request_context, request


SITE_NAME = "XXX World"
DEFAULT_SITE_URL = "https://for-xvid.vercel.app"
TRACKING_PARAMETERS = {
    "fbclid", "gclid", "dclid", "msclkid", "ref", "source",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
}


def site_origin() -> str:
    """Return the configured public origin, avoiding Vercel preview URLs as canonicals."""
    value = os.getenv("SITE_URL", "").strip().rstrip("/") or DEFAULT_SITE_URL
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return DEFAULT_SITE_URL
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def absolute_url(path_or_url: str | None, origin: str | None = None) -> str:
    """Resolve a site path against the canonical origin while preserving external media URLs."""
    value = str(path_or_url or "/").strip()
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    base = (origin or site_origin()).rstrip("/")
    if not value.startswith("/"):
        value = "/" + value
    return base + value


def canonical_path_for_request() -> str:
    """Build a canonical path without tracking parameters or cursor-history bookkeeping."""
    if not has_request_context():
        return "/"

    ignored = TRACKING_PARAMETERS | {"history"}
    pairs: list[tuple[str, str]] = []
    for key in sorted(request.args.keys()):
        if key in ignored or key.lower().startswith("utm_"):
            continue
        pairs.extend((key, value) for value in request.args.getlist(key))

    # /browse and /browse?section=popular represent the same default collection.
    if request.path == "/browse" and not any(key == "section" for key, _ in pairs):
        pairs.append(("section", "popular"))
        pairs.sort()

    query = urlencode(pairs, doseq=True)
    return request.path + ("?" + query if query else "")


def _clean_text(value: object, fallback: str = "") -> str:
    return " ".join(str(value or fallback).split()).strip()


def _as_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def iso_duration(seconds: object) -> str | None:
    """Convert a known positive duration in seconds into Schema.org ISO 8601 duration."""
    total = _as_positive_int(seconds)
    if total is None:
        return None
    hours, remainder = divmod(total, 3600)
    minutes, seconds_left = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}H")
    if minutes:
        parts.append(f"{minutes}M")
    if seconds_left or not parts:
        parts.append(f"{seconds_left}S")
    return "PT" + "".join(parts)


def _source_upload_date(video: dict) -> str | None:
    """Use only source upload timestamps; never substitute ingestion or deployment time."""
    value = video.get("upload_date") or video.get("uploaded_at")
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            # Date-only ISO strings are valid uploadDate values too.
            candidate = str(value).strip()
            if len(candidate) == 10:
                try:
                    datetime.strptime(candidate, "%Y-%m-%d")
                except ValueError:
                    return None
                return candidate
            return None
    if parsed.tzinfo is None:
        # Keep source precision but do not invent a timezone.
        return parsed.isoformat()
    return parsed.isoformat(timespec="seconds")


def video_object(video: dict, page_path: str | None = None) -> dict:
    """Build a VideoObject from fields the application actually has."""
    title = _clean_text(video.get("title"), "Video")
    video_url = absolute_url(page_path or video.get("video_href") or "/")
    thumbnail = absolute_url(video.get("thumbnail_url")) if video.get("thumbnail_url") else None
    item: dict = {
        "@type": "VideoObject",
        "@id": video_url + "#video",
        "name": title,
        "url": video_url,
        "description": _clean_text(
            video.get("seo_description"),
            f"Watch {title} on {SITE_NAME}.",
        ),
        "inLanguage": "en",
        "isPartOf": {"@id": site_origin() + "/#website"},
        "mainEntityOfPage": {"@id": video_url + "#webpage"},
    }
    if thumbnail:
        item["thumbnailUrl"] = [thumbnail]
    duration = iso_duration(video.get("duration_seconds"))
    if duration:
        item["duration"] = duration
    upload_date = _source_upload_date(video)
    if upload_date:
        item["uploadDate"] = upload_date
    view_count = _as_positive_int(video.get("views"))
    if view_count is not None:
        item["interactionStatistic"] = {
            "@type": "InteractionCounter",
            "interactionType": {"@type": "WatchAction"},
            "userInteractionCount": view_count,
        }
    tags = video.get("tags")
    if isinstance(tags, (list, tuple)):
        keywords = [_clean_text(tag) for tag in tags if _clean_text(tag)]
        if keywords:
            item["keywords"] = keywords
    # Include real playback URLs only if a future query supplies them explicitly.
    for field in ("content_url", "embed_url"):
        value = video.get(field)
        if isinstance(value, str) and urlsplit(value).scheme in {"http", "https"} and urlsplit(value).netloc:
            item["contentUrl" if field == "content_url" else "embedUrl"] = value
    return item


def video_item_list_schema(videos: list[dict], name: str, list_path: str, fragment: str = "itemlist") -> dict:
    """Represent a visible collection of videos as an ordered ItemList."""
    list_url = absolute_url(list_path)
    elements = []
    for position, video in enumerate(videos, start=1):
        title = _clean_text(video.get("title"), "Video")
        item_url = absolute_url(video.get("video_href") or "/")
        thumbnail = absolute_url(video.get("thumbnail_url")) if video.get("thumbnail_url") else None
        listed_video = {
            "@type": "VideoObject",
            "@id": item_url + "#video",
            "name": title,
            "url": item_url,
        }
        if thumbnail:
            listed_video["thumbnailUrl"] = [thumbnail]
        duration = iso_duration(video.get("duration_seconds"))
        if duration:
            listed_video["duration"] = duration
        elements.append({
            "@type": "ListItem",
            "position": position,
            "item": listed_video,
        })
    return {
        "@type": "ItemList",
        "@id": list_url + "#" + fragment,
        "name": _clean_text(name, "Videos"),
        "url": list_url,
        "numberOfItems": len(elements),
        "itemListOrder": "https://schema.org/ItemListOrderAscending",
        "itemListElement": elements,
    }


def named_item_list_schema(items: list[dict], name: str, list_path: str, fragment: str = "itemlist") -> dict:
    """Represent visible category links or image frames as an ItemList."""
    list_url = absolute_url(list_path)
    elements = []
    for position, item in enumerate(items, start=1):
        item_url = absolute_url(item.get("url") or "/")
        if item.get("image"):
            thing = {
                "@type": "ImageObject",
                "@id": item_url,
                "url": item_url,
                "contentUrl": item_url,
                "name": _clean_text(item.get("name"), "Image"),
            }
        else:
            thing = {
                "@type": "Thing",
                "@id": item_url,
                "url": item_url,
                "name": _clean_text(item.get("name"), "Item"),
            }
        elements.append({"@type": "ListItem", "position": position, "item": thing})
    return {
        "@type": "ItemList",
        "@id": list_url + "#" + fragment,
        "name": _clean_text(name, "Items"),
        "url": list_url,
        "numberOfItems": len(elements),
        "itemListOrder": "https://schema.org/ItemListOrderAscending",
        "itemListElement": elements,
    }


def build_page_seo(
    *,
    title: str,
    description: str,
    canonical_path: str | None = None,
    page_type: str = "WebPage",
    image: str | None = None,
    breadcrumbs: list[tuple[str, str]] | None = None,
    main_entity: dict | None = None,
    additional_nodes: list[dict] | None = None,
    about: dict | None = None,
    noindex: bool = False,
    og_type: str = "website",
) -> dict:
    """Build canonical/meta fields and a JSON-LD graph for a single HTML page."""
    origin = site_origin()
    canonical = absolute_url(canonical_path or canonical_path_for_request(), origin)
    clean_title = _clean_text(title, SITE_NAME)
    clean_description = _clean_text(description, f"Discover videos and categories on {SITE_NAME}.")[:320]
    logo_base = os.getenv("STATIC_ASSETS_BASE_URL", "").strip().rstrip("/")
    organization: dict = {
        "@type": "Organization",
        "@id": origin + "/#organization",
        "name": SITE_NAME,
        "url": origin + "/",
    }
    if logo_base:
        organization["logo"] = {
            "@type": "ImageObject",
            "url": logo_base + "/xxxworld-icon-256.webp",
            "width": 256,
            "height": 256,
        }
    website = {
        "@type": "WebSite",
        "@id": origin + "/#website",
        "url": origin + "/",
        "name": SITE_NAME,
        "inLanguage": "en",
        "publisher": {"@id": origin + "/#organization"},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": origin + "/search?q={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
    }

    page_id = canonical + "#webpage"
    page: dict = {
        "@type": page_type,
        "@id": page_id,
        "url": canonical,
        "name": clean_title,
        "description": clean_description,
        "inLanguage": "en",
        "isPartOf": {"@id": origin + "/#website"},
        "publisher": {"@id": origin + "/#organization"},
    }
    if image:
        page["primaryImageOfPage"] = {
            "@type": "ImageObject",
            "url": absolute_url(image, origin),
        }
    if main_entity:
        page["mainEntity"] = main_entity
    if about:
        page["about"] = about

    graph = [organization, website, page]
    if breadcrumbs and len(breadcrumbs) >= 2:
        breadcrumb_id = canonical + "#breadcrumb"
        page["breadcrumb"] = {"@id": breadcrumb_id}
        graph.append({
            "@type": "BreadcrumbList",
            "@id": breadcrumb_id,
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": position,
                    "name": _clean_text(label),
                    "item": absolute_url(path, origin),
                }
                for position, (label, path) in enumerate(breadcrumbs, start=1)
            ],
        })
    for node in additional_nodes or []:
        if node:
            graph.append(node)

    return {
        "title": clean_title,
        "description": clean_description,
        "canonical_url": canonical,
        "image": absolute_url(image, origin) if image else "",
        "robots": (
            "noindex,follow" if noindex else "index,follow,max-image-preview:large"
        ),
        "og_type": og_type,
        "structured_data": {"@context": "https://schema.org", "@graph": graph},
    }
