import hashlib
import unittest
from unittest.mock import patch

from web.db import preferred_video_shard
from web.catalog import list_catalog


class ServingShardTests(unittest.TestCase):
    def test_hash_partition_matches_original_collector_rule(self):
        for source_url in (
            "/abc/video-one",
            "/channel-two/video-b",
            "/another/id-three",
            "/creator/long-title",
        ):
            bucket = hashlib.sha256(source_url.encode("utf-8")).digest()[0] % 2
            expected = "DATABASE_URL_3" if bucket == 0 else "DATABASE_URL_4"
            self.assertEqual(preferred_video_shard(source_url), expected)

    def test_hash_partition_spreads_urls_across_both_shards(self):
        shards = {
            preferred_video_shard(f"/video/{index}")
            for index in range(100)
        }
        self.assertEqual(shards, {"DATABASE_URL_3", "DATABASE_URL_4"})

    def test_catalog_merges_shard_pages_and_uses_cursor_pagination(self):
        shard_rows = {
            "db3": [
                {"url": "/a", "title": "A", "rating_percentage": 90.0, "views": 900, "tags": ["one"]},
                {"url": "/c", "title": "C", "rating_percentage": 80.0, "views": 800, "tags": ["two"]},
                {"url": "/e", "title": "E", "rating_percentage": 70.0, "views": 700, "tags": ["three"]},
            ],
            "db4": [
                {"url": "/b", "title": "B", "rating_percentage": 95.0, "views": 950, "tags": ["one"]},
                {"url": "/d", "title": "D", "rating_percentage": 85.0, "views": 850, "tags": ["two"]},
                {"url": "/f", "title": "F", "rating_percentage": 75.0, "views": 750, "tags": ["three"]},
            ],
        }

        def fake_fetch_rows(database_url, sql, params=(), **kwargs):
            if "db3" in database_url:
                return shard_rows["db3"][: params[-1]]
            return shard_rows["db4"][: params[-1]]

        with patch("web.catalog.catalog_sources", return_value=[
            ("DATABASE_URL_3", "db3"),
            ("DATABASE_URL_4", "db4"),
        ]), patch("web.catalog.fetch_rows", side_effect=fake_fetch_rows):
            result = list_catalog(sort="views", limit=2)

        self.assertEqual([row["url"] for row in result["items"]], ["/b", "/a"])
        self.assertTrue(result["has_more"])
        self.assertTrue(result["next_cursor"])

    def test_category_filter_uses_tags_array_containment_index(self):
        with patch("web.catalog.catalog_sources", return_value=[
            ("DATABASE_URL_3", "db3"),
            ("DATABASE_URL_4", "db4"),
        ]), patch("web.catalog.fetch_rows", return_value=[]) as fetch:
            result = list_catalog(tag="blond", limit=12)

        self.assertEqual(result["items"], [])
        self.assertTrue(fetch.call_args.args[1].find("tags @> ARRAY[%s]::text[]") >= 0)


    def test_search_combines_full_text_and_exact_tag_indexes(self):
        with patch("web.catalog.catalog_sources", return_value=[
            ("DATABASE_URL_3", "db3"),
            ("DATABASE_URL_4", "db4"),
        ]), patch("web.catalog.fetch_rows", return_value=[]) as fetch:
            result = list_catalog(search="blond", limit=12)

        self.assertEqual(result["items"], [])
        sql = fetch.call_args.args[1]
        self.assertIn("websearch_to_tsquery", sql)
        self.assertIn("OR tags @> ARRAY[%s]::text[]", sql)


if __name__ == "__main__":
    unittest.main()
