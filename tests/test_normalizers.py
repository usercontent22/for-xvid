import json
import tempfile
import unittest
from pathlib import Path

from scripts.collect_channel_videos import (
    clean_img, clean_url, duration_seconds, integer_views,
    normalize, rating_percentage, channel_slug, bucket_for,
    load_channels, reported_video_count,
)


class MetadataNormalizationTests(unittest.TestCase):
    def test_removes_click_and_channel_prefix_from_url(self):
        self.assertEqual(
            clean_url("/prof-video-click/upload/cum-for-cover/hvtebdd/shy_misty_in_preview"),
            "/hvtebdd/shy_misty_in_preview",
        )

    def test_accepts_full_video_url(self):
        self.assertEqual(
            clean_url("https://www.xvideos.com/prof-video-click/upload/channel/abc123/video_slug"),
            "/abc123/video_slug",
        )

    def test_removes_standard_thumbnail_domain(self):
        self.assertEqual(
            clean_img("https://thumb-cdn77.xvideos-cdn.com/uuid/0/xv_28_t.jpg"),
            "/uuid/0/xv_28_t.jpg",
        )

    def test_adds_g_prefix_to_gcore_thumbnail(self):
        self.assertEqual(
            clean_img("https://thumbs-gcore.xvideos-cdn.com/uuid/0/xv_20_t.jpg"),
            "g/uuid/0/xv_20_t.jpg",
        )

    def test_views(self):
        self.assertEqual(integer_views("907.6k"), 907600)
        self.assertEqual(integer_views("1.1M"), 1100000)
        self.assertEqual(integer_views("1,234"), 1234)

    def test_duration(self):
        self.assertEqual(duration_seconds("5 min"), 300)
        self.assertEqual(duration_seconds("1 h 5 min 10 sec"), 3910)
        self.assertEqual(duration_seconds("12:34"), 754)

    def test_rating(self):
        self.assertEqual(rating_percentage("99%"), 99.0)
        self.assertIsNone(rating_percentage("not available"))

    def test_channel_slug(self):
        self.assertEqual(channel_slug("https://www.xvideos.com/channels/example"), "example")
        self.assertEqual(channel_slug("/example"), "example")

    def test_required_metadata_and_missing_tags(self):
        row = normalize({
            "u": "/prof-video-click/upload/channel/abc123/video_slug",
            "i": "https://thumb-cdn77.xvideos-cdn.com/uuid/0/xv_1_t.jpg",
            "tf": "Video title", "r": "100%", "n": "1.1M",
            "d": "5 min", "pn": "Channel display name",
        }, "fallback")
        self.assertEqual(row["url"], "/abc123/video_slug")
        self.assertEqual(row["title"], "Video title")
        self.assertEqual(row["img"], "/uuid/0/xv_1_t.jpg")
        self.assertEqual(row["rating"], "100%")
        self.assertEqual(row["rating_percentage"], 100.0)
        self.assertEqual(row["views"], 1100000)
        self.assertEqual(row["duration"], "5 min")
        self.assertEqual(row["duration_seconds"], 300)
        self.assertEqual(row["channel_name"], "Channel display name")
        self.assertEqual(row["tags"], [])

    def test_partition_is_stable(self):
        self.assertEqual(bucket_for("/abc123/video_slug"), bucket_for("/abc123/video_slug"))
        self.assertIn(bucket_for("/abc123/video_slug"), (0, 1))


    def test_null_inventory_counts_are_probed_but_known_small_channels_are_skipped(self):
        inventory = [
            {"href": "/unknown_count", "total_videos": None},
            {"href": "/string_null_count", "total_videos": "null"},
            {"href": "/nine_videos", "total_videos": 9},
            {"href": "/ten_videos", "total_videos": 10},
            {"href": "/large_channel", "total_videos": "12,345"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "channels.json"
            path.write_text(json.dumps(inventory), encoding="utf-8")
            channels = load_channels(str(path), minimum=10)
        self.assertEqual(
            channels,
            ["large_channel", "string_null_count", "ten_videos", "unknown_count"],
        )

    def test_actual_api_count_overrides_null_inventory_and_controls_threshold(self):
        self.assertEqual(
            reported_video_count({"nb_videos": 10, "videos": [{}, {}]}),
            10,
        )
        self.assertEqual(
            reported_video_count({"nb_videos": 9, "videos": [{}] * 20}),
            9,
        )

    def test_missing_api_count_falls_back_to_returned_page_count(self):
        self.assertEqual(reported_video_count({"nb_videos": None, "videos": [{}] * 10}), 10)
        self.assertEqual(reported_video_count({"videos": []}), 0)


if __name__ == "__main__":
    unittest.main()