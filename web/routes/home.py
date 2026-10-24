"""Public homepage, discoverable snapshot URLs and sitemap."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from flask import Blueprint, Response, render_template, request, url_for

from web.catalog import catalog_configured
from web.ids import encode_token, slugify
from web.media import decorate_video
from web.repositories.snapshots import load_homepage_data
from web.seo import (
    absolute_url,
    build_page_seo,
    named_item_list_schema,
    video_item_list_schema,
)


home = Blueprint("home", __name__)


def _videos(section_data: dict) -> list[dict]:
    items = section_data.get("items", []) if isinstance(section_data, dict) else []
    return [decorate_video(item) for item in items if isinstance(item, dict) and item.get("url")]


@home.get("/")
def index():
    data = load_homepage_data()
    featured = _videos(data.get("featured", {}))
    popular = _videos(data.get("popular", {}))
    discovery = _videos(data.get("discovery", {}))

    # Prioritize the main feed and avoid repeats across the visible homepage sections.
    primary_section = "popular" if popular else ("featured" if featured else "discovery")
    primary_videos = {"popular": popular, "featured": featured, "discovery": discovery}[primary_section]
    primary_title = {"popular": "Popular videos", "featured": "Featured videos", "discovery": "Discover videos"}[primary_section]

    seen_urls = {
        str(item.get("url"))
        for item in primary_videos[:24]
        if item.get("url")
    }
    featured = [item for item in featured if str(item.get("url") or "") not in seen_urls]
    seen_urls.update(
        str(item.get("url"))
        for item in featured[:12]
        if item.get("url")
    )
    discovery = [item for item in discovery if str(item.get("url") or "") not in seen_urls]

    tag_items = data.get("tag_catalog", {}).get("items", [])
    if not isinstance(tag_items, list):
        tag_items = []
    tags = [item for item in tag_items if isinstance(item, dict) and item.get("tag")]
    primary_list = video_item_list_schema(
        primary_videos[:24],
        primary_title,
        "/",
        fragment="popular-videos-list",
    )
    additional_schema = [primary_list]

    visible_featured = featured[:12] if featured and popular else []
    if visible_featured:
        additional_schema.append(
            video_item_list_schema(
                visible_featured,
                "Featured videos",
                "/",
                fragment="featured-videos-list",
            )
        )

    visible_discovery = discovery[:12] if discovery and (popular or featured) else []
    if visible_discovery:
        additional_schema.append(
            video_item_list_schema(
                visible_discovery,
                "More to discover",
                "/",
                fragment="discovery-videos-list",
            )
        )

    visible_tags = tags[:18]
    category_items = [
        {
            "name": str(item["tag"]),
            "url": f"/category/{slugify(str(item['tag']))}--{encode_token(str(item['tag']))}",
        }
        for item in visible_tags
    ]
    if category_items:
        additional_schema.append(
            named_item_list_schema(
                category_items,
                "Video categories",
                "/",
                fragment="categories-list",
            )
        )

    seo = build_page_seo(
        title=f"{primary_title} — XXX World",
        description="Browse popular videos, discover new picks, and explore video categories on XXX World.",
        canonical_path="/",
        page_type="CollectionPage",
        main_entity={"@id": absolute_url("/") + "#popular-videos-list"},
        additional_nodes=additional_schema,
    )
    return render_template(
        "home.html",
        seo=seo,
        featured=featured,
        popular=popular,
        primary_section=primary_section,
        primary_title=primary_title,
        discovery=discovery,
        primary_videos=primary_videos,
        tags=visible_tags,
        feed_date=data.get("featured", {}).get("date", ""),
        catalog_ready=catalog_configured(),
        snapshots_ready=bool(featured or popular or discovery),
    )


@home.get("/sitemap.xml")
def sitemap():
    data = load_homepage_data()
    root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    site_url = os.getenv("SITE_URL", "").strip().rstrip("/")
    today = datetime.now(timezone.utc).date().isoformat()
    locs = {
        "/",
        "/browse?section=featured",
        "/browse?section=popular",
        "/browse?section=discovery",
    }

    for section in ("featured", "popular", "discovery"):
        for item in data.get(section, {}).get("items", []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            source_url = str(item["url"])
            locs.add(f"/video/{slugify(item.get('title'))}--{encode_token(source_url)}")
            locs.add(f"/images/{slugify(item.get('title'))}--{encode_token(source_url)}")
            channel_name = str(item.get("channel_name") or "").strip()
            if channel_name:
                locs.add(f"/channel/{slugify(channel_name)}--{encode_token(channel_name)}")

    for item in data.get("tag_catalog", {}).get("items", []):
        if isinstance(item, dict) and item.get("tag"):
            tag = str(item["tag"])
            locs.add(f"/category/{slugify(tag)}--{encode_token(tag)}")

    origin = site_url or url_for("home.index", _external=True).rstrip("/")
    for loc in sorted(locs):
        entry = SubElement(root, "url")
        SubElement(entry, "loc").text = origin + loc if loc != "/" else origin + "/"
        SubElement(entry, "lastmod").text = today

    body = tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(body, mimetype="application/xml")


@home.get("/robots.txt")
def robots():
    origin = os.getenv("SITE_URL", "").strip().rstrip("/") or request.url_root.rstrip("/")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /healthz\n"
        f"Sitemap: {origin}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")
