"""Snapshot browsing and optional indexed full-catalog search."""
from __future__ import annotations

from math import ceil

from flask import Blueprint, abort, redirect, render_template, request, url_for

from web.catalog import catalog_configured, list_catalog
from web.ids import decode_history, decode_token, encode_history, normalize_channel_key
from web.media import decorate_video
from web.repositories.snapshots import load_homepage_data


browse = Blueprint("browse", __name__)
PAGE_SIZE = 12
CATALOG_PAGE_SIZE = 24
VALID_SECTIONS = {"featured", "popular", "discovery"}


def _snapshot_pool(data: dict) -> list[dict]:
    seen: set[str] = set()
    output: list[dict] = []
    for section in ("featured", "popular", "discovery"):
        for item in data.get(section, {}).get("items", []):
            if not isinstance(item, dict) or not item.get("url") or item["url"] in seen:
                continue
            seen.add(item["url"])
            output.append(item)
    return output


def _filter_items(items: list[dict], *, search: str = "", channel: str = "", tag: str = "") -> list[dict]:
    q = search.casefold().strip()
    channel_key = normalize_channel_key(channel)
    tag_key = str(tag).casefold().strip()
    result = []
    for item in items:
        title = str(item.get("title") or "").casefold()
        item_channel = str(item.get("channel_name") or "")
        tags = [str(value).casefold() for value in (item.get("tags") or [])]
        matches = (
            (not q or q in title or q in item_channel.casefold() or any(q in value for value in tags))
            and (not channel or normalize_channel_key(item_channel) == channel_key)
            and (not tag or tag_key in tags)
        )
        if matches:
            result.append(item)
    return result


def _page_url(endpoint: str, values: dict, cursor: str | None, history: list[str], next_cursor: str | None):
    result = {"previous_url": None, "next_url": None}
    if history:
        previous_cursor = history[-1] or None
        result["previous_url"] = url_for(
            endpoint, **values, cursor=previous_cursor, history=encode_history(history[:-1])
        )
    if next_cursor:
        next_history = history + [cursor or ""]
        result["next_url"] = url_for(
            endpoint, **values, cursor=next_cursor, history=encode_history(next_history)
        )
    return result


def _fallback_page(
    *,
    title: str,
    description: str,
    items: list[dict],
    endpoint: str,
    values: dict,
    page: int,
    warning: bool = True,
    is_channel: bool = False,
):
    # Only a small prepared pool is paginated here, never a database result set.
    count = len(items)
    pages = max(1, ceil(count / PAGE_SIZE))
    page = min(max(page, 1), pages)
    start = (page - 1) * PAGE_SIZE
    decorated = [decorate_video(item) for item in items[start:start + PAGE_SIZE]]
    avatar_url = next((item.get("thumbnail_url") for item in decorated if item.get("thumbnail_url")), "")
    return render_template(
        "listing.html",
        title=title,
        description=description,
        videos=decorated,
        catalog_ready=False,
        snapshot_notice=warning,
        is_channel=is_channel,
        channel_avatar_url=avatar_url,
        tag_items=[],
        page=page,
        pages=pages,
        previous_url=url_for(endpoint, **values, page=page - 1) if page > 1 else None,
        next_url=url_for(endpoint, **values, page=page + 1) if page < pages else None,
        empty=not decorated,
    )


def _catalog_page(
    *,
    endpoint: str,
    values: dict,
    title: str,
    description: str,
    sort: str = "views",
    channel: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    is_channel: bool = False,
):
    cursor = request.args.get("cursor")
    history = decode_history(request.args.get("history"))
    result = list_catalog(
        sort=sort,
        cursor=cursor,
        limit=CATALOG_PAGE_SIZE,
        channel=channel,
        tag=tag,
        search=search,
    )
    if result is None:
        return None
    decorated = [decorate_video(item) for item in result["items"]]
    nav = _page_url(endpoint, values, cursor, history, result.get("next_cursor"))
    avatar_url = next((item.get("thumbnail_url") for item in decorated if item.get("thumbnail_url")), "")
    return render_template(
        "listing.html",
        title=title,
        description=description,
        videos=decorated,
        catalog_ready=True,
        snapshot_notice=False,
        is_channel=is_channel,
        channel_avatar_url=avatar_url,
        tag_items=[],
        previous_url=nav["previous_url"],
        next_url=nav["next_url"],
        page=None,
        pages=None,
        empty=not decorated,
    )


@browse.get("/browse")
def listing():
    section = request.args.get("section", "popular")
    if section not in VALID_SECTIONS:
        section = "popular"
    page = request.args.get("page", default=1, type=int) or 1
    title = {"featured": "Featured videos", "popular": "Popular videos", "discovery": "Discover"}[section]
    description = "A focused collection refreshed from the prepared video feeds."
    if catalog_configured():
        result = _catalog_page(
            endpoint="browse.listing",
            values={"section": section},
            title=title,
            description=description,
            sort="rating" if section == "featured" else "views",
        )
        if result is not None:
            return result
    data = load_homepage_data()
    items = data.get(section, {}).get("items", [])
    return _fallback_page(
        title=title,
        description=description,
        items=items if isinstance(items, list) else [],
        endpoint="browse.listing",
        values={"section": section},
        page=page,
    )


@browse.get("/search")
def search():
    query = (request.args.get("q") or "").strip()[:120]
    if not query:
        return redirect(url_for("home.index"))
    if catalog_configured():
        result = _catalog_page(
            endpoint="browse.search",
            values={"q": query},
            title=f"Search: {query}",
            description="Search titles, channel names, and indexed tags in the video catalog.",
            search=query,
        )
        if result is not None:
            return result
    data = load_homepage_data()
    filtered = _filter_items(_snapshot_pool(data), search=query)
    return _fallback_page(
        title=f"Search: {query}",
        description="Results from the prepared homepage highlights.",
        items=filtered,
        endpoint="browse.search",
        values={"q": query},
        page=request.args.get("page", default=1, type=int) or 1,
    )


@browse.get("/category/<slug>--<token>")
def category(slug: str, token: str):
    tag = decode_token(token)
    if not tag:
        abort(404)
    if catalog_configured():
        result = _catalog_page(
            endpoint="browse.category",
            values={"slug": slug, "token": token},
            title=tag,
            description=f"Videos tagged {tag}.",
            tag=tag,
        )
        if result is not None:
            return result
    data = load_homepage_data()
    filtered = _filter_items(_snapshot_pool(data), tag=tag)
    return _fallback_page(
        title=tag,
        description=f"Explore highlights tagged {tag}.",
        items=filtered,
        endpoint="browse.category",
        values={"slug": slug, "token": token},
        page=request.args.get("page", default=1, type=int) or 1,
    )


@browse.get("/channel/<slug>--<token>")
def channel(slug: str, token: str):
    channel_name = decode_token(token)
    if not channel_name:
        abort(404)
    if catalog_configured():
        result = _catalog_page(
            endpoint="browse.channel",
            values={"slug": slug, "token": token},
            title=channel_name,
            description=f"Browse videos from {channel_name}.",
            channel=channel_name,
            is_channel=True,
        )
        if result is not None:
            return result
    data = load_homepage_data()
    filtered = _filter_items(_snapshot_pool(data), channel=channel_name)
    return _fallback_page(
        title=channel_name,
        description=f"Highlights from {channel_name}.",
        items=filtered,
        endpoint="browse.channel",
        values={"slug": slug, "token": token},
        page=request.args.get("page", default=1, type=int) or 1,
        is_channel=True,
    )
