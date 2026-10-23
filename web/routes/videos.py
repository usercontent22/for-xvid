"""Stable video detail routes and numbered thumbnail-image galleries."""
from __future__ import annotations

from flask import Blueprint, abort, render_template

from web.db import DatabaseUnavailable, find_video
from web.catalog import list_catalog
from web.ids import decode_token
from web.media import decorate_video, image_variant_url
from web.repositories.snapshots import load_homepage_data
from web.seo import (
    absolute_url,
    build_page_seo,
    named_item_list_schema,
    video_object,
)


videos = Blueprint("videos", __name__)


def _channel_videos(video: dict, limit: int = 4) -> list[dict]:
    channel = str(video.get("channel_name") or "").strip()
    if not channel:
        return []
    page = list_catalog(sort="views", channel=channel, limit=min(max(limit + 1, 1), 8))
    if not page:
        return []
    current_url = video.get("url")
    picks = [
        decorate_video(item)
        for item in page.get("items", [])
        if item.get("url") and item.get("url") != current_url
    ]
    return picks[:limit]


def _related(video: dict, limit: int = 8) -> list[dict]:
    data = load_homepage_data()
    wanted_tags = {str(tag).casefold() for tag in (video.get("tags") or [])}
    seen = {video.get("url")}
    candidates = []
    for section in ("featured", "popular", "discovery"):
        for item in data.get(section, {}).get("items", []):
            if not isinstance(item, dict) or not item.get("url") or item["url"] in seen:
                continue
            seen.add(item["url"])
            item_tags = {str(tag).casefold() for tag in (item.get("tags") or [])}
            overlap = len(wanted_tags & item_tags)
            if overlap:
                try:
                    view_count = int(item.get("views") or 0)
                except (TypeError, ValueError):
                    view_count = 0
                candidates.append((overlap, view_count, item))
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [decorate_video(row[2]) for row in candidates[:limit]]


def _load(token: str) -> dict:
    source_url = decode_token(token)
    if (
        not source_url
        or len(source_url) > 2048
        or "\x00" in source_url
        or source_url.startswith("//")
    ):
        abort(404)
    try:
        # The value comes back from our own opaque route token and is used only
        # as a parameter to an exact primary-key lookup, never as a redirect.
        video = find_video(source_url)
    except DatabaseUnavailable:
        return {}
    if not video:
        abort(404)
    return decorate_video(video)


@videos.get("/video/<slug>--<token>")
def detail(slug: str, token: str):
    video = _load(token)
    if not video:
        seo = build_page_seo(
            title="Video temporarily unavailable — XXX World",
            description="This video could not be loaded right now.",
            noindex=True,
        )
        return render_template(
            "video_detail.html",
            seo=seo,
            video=None,
            related=[],
            channel_videos=[],
            database_unavailable=True,
            page_title="Video temporarily unavailable",
        ), 503

    page_path = video["video_href"]
    title = video.get("title") or "Video"
    description = f"Watch {title}, explore its categories and channel, and find related videos on XXX World."
    video["seo_description"] = description
    structured_video = video_object(video, page_path)
    video_id = absolute_url(page_path) + "#video"
    seo = build_page_seo(
        title=f"{title} — XXX World",
        description=description,
        canonical_path=page_path,
        page_type="WebPage",
        image=video.get("thumbnail_url"),
        breadcrumbs=[
            ("Home", "/"),
            ("Popular videos", "/browse?section=popular"),
            (title, page_path),
        ],
        main_entity={"@id": video_id} if video.get("thumbnail_url") else None,
        additional_nodes=[structured_video] if video.get("thumbnail_url") else [],
        og_type="video.other",
    )
    return render_template(
        "video_detail.html",
        seo=seo,
        video=video,
        related=_related(video),
        channel_videos=_channel_videos(video),
        database_unavailable=False,
        page_title=title,
    )


@videos.get("/images/<slug>--<token>")
def image_gallery(slug: str, token: str):
    video = _load(token)
    if not video:
        seo = build_page_seo(
            title="Image gallery temporarily unavailable — XXX World",
            description="This image gallery could not be loaded right now.",
            noindex=True,
        )
        return render_template(
            "video_detail.html",
            seo=seo,
            video=None,
            related=[],
            database_unavailable=True,
            page_title="Video temporarily unavailable",
        ), 503
    frames = [
        {"number": number, "url": image_variant_url(video.get("img"), number)}
        for number in range(1, 31)
    ]
    frames = [frame for frame in frames if frame["url"]]
    page_path = video["image_gallery_href"]
    title = f"Images: {video.get('title') or 'Video'}"
    description = f"Browse available image frames from {video.get('title') or 'this video'} on XXX World."
    frame_items = [
        {"name": f"{video.get('title') or 'Video'} — frame {frame['number']}", "url": frame["url"], "image": True}
        for frame in frames
    ]
    image_list = named_item_list_schema(
        frame_items,
        title,
        page_path,
        fragment="image-frames-list",
    )
    seo = build_page_seo(
        title=f"{title} — XXX World",
        description=description,
        canonical_path=page_path,
        page_type="CollectionPage",
        image=video.get("thumbnail_url"),
        breadcrumbs=[
            ("Home", "/"),
            (video.get("title") or "Video", video["video_href"]),
            ("Image frames", page_path),
        ],
        main_entity={"@id": image_list["@id"]} if frame_items else None,
        additional_nodes=[image_list] if frame_items else [],
        about={"@type": "Thing", "name": video.get("title") or "Video"},
    )
    return render_template(
        "image_gallery.html",
        seo=seo,
        video=video,
        frames=frames,
        page_title=title,
    )
