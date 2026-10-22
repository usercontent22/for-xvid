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
    tag_items = data.get("tag_catalog", {}).get("items", [])
    if not isinstance(tag_items, list):
        tag_items = []
    tags = [item for item in tag_items if isinstance(item, dict) and item.get("tag")]
    return render_template(
        "home.html",
        featured=featured,
        popular=popular,
        discovery=discovery,
        tags=tags[:18],
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
