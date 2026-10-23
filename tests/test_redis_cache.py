import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from web.cache import cache_key
from web.catalog import list_catalog
from web.repositories.snapshots import load_homepage_data


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key):
        return self.values.get(key)

    def mget(self, keys):
        return [self.values.get(key) for key in keys]


def payload(section, feed_date, items=None):
    return {
        "date": feed_date,
        "section": section,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items if items is not None else [{"url": f"/{section}/video", "title": section}],
    }


class RedisHomepageCacheTests(unittest.TestCase):
    def test_homepage_reads_redis_before_db5(self):
        today = datetime.now(timezone.utc).date().isoformat()
        values = {}
        for section in ("featured", "popular", "discovery", "tags"):
            values[cache_key(f"homepage:feed:{today}:{section}")] = json.dumps(payload(section, today))
        values[cache_key("homepage:tag_catalog")] = json.dumps(payload("tag_catalog", today, [{"tag": "comedy"}]))
        fake = FakeRedis(values)

        with (
            patch("web.repositories.snapshots.get_redis_client", return_value=fake),
            patch("web.repositories.snapshots._load_db5_homepage_data", side_effect=AssertionError("DB5 should not be read on a cache hit")),
        ):
            result = load_homepage_data()

        self.assertEqual(result["featured"]["items"][0]["url"], "/featured/video")
        self.assertEqual(result["tag_catalog"]["items"][0]["tag"], "comedy")

    def test_homepage_falls_back_to_db5_on_redis_miss(self):
        fallback = {
            "featured": payload("featured", datetime.now(timezone.utc).date().isoformat()),
            "popular": payload("popular", datetime.now(timezone.utc).date().isoformat()),
            "discovery": payload("discovery", datetime.now(timezone.utc).date().isoformat()),
        }
        with (
            patch("web.repositories.snapshots.get_redis_client", return_value=FakeRedis()),
            patch("web.repositories.snapshots._load_db5_homepage_data", return_value=fallback),
            patch("web.repositories.snapshots.cache_set_json") as cache_write,
        ):
            result = load_homepage_data()

        self.assertEqual(result, fallback)
        self.assertGreater(cache_write.call_count, 0)


class CatalogRedisCacheTests(unittest.TestCase):
    def test_catalog_returns_cached_page_without_querying_neon(self):
        cached = {
            "items": [{"url": "/popular/video", "title": "Cached video"}],
            "has_more": False,
            "next_cursor": None,
        }
        with (
            patch("web.catalog.catalog_sources", return_value=[("DATABASE_URL_3", "postgres://unused")]),
            patch("web.catalog.cache_get_json", return_value=cached),
            patch("web.catalog._fetch_shard") as fetch_shard,
        ):
            result = list_catalog(sort="views", limit=24)

        self.assertEqual(result, cached)
        fetch_shard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
