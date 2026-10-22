import unittest
from datetime import date

from scripts.build_homepage_snapshots import diverse_select, json_video


class HomepageSnapshotTests(unittest.TestCase):
    def test_video_cards_filter_generic_tags_and_keep_rank_fields(self):
        card = json_video((
            "/abc/video", "Example video", "/thumb.jpg", 95, 120000,
            420, "Channel A", ["video", "amateur", "x-videos", "homemade"],
        ))
        self.assertIsNotNone(card)
        self.assertEqual(card["tags"], ["amateur", "homemade"])
        self.assertEqual(card["views"], 120000)
        self.assertEqual(card["rating_percentage"], 95.0)
        self.assertNotIn("_channel_key", {key for key in card if not key.startswith("_")})

    def test_cards_require_title_and_thumbnail(self):
        self.assertIsNone(json_video(("/abc/video", "", "/thumb.jpg", 90, 100, 60, "Channel", [])))
        self.assertIsNone(json_video(("/abc/video", "Title", None, 90, 100, 60, "Channel", [])))

    def test_diverse_select_does_not_repeat_urls_between_sections(self):
        videos = []
        for index in range(30):
            videos.append({
                "url": f"/id{index}/slug",
                "title": f"Title {index}",
                "img": f"/thumb{index}.jpg",
                "views": 10000 - index,
                "quality_score": 0.9 - index / 100,
                "_channel_key": f"channel-{index // 3}",
                "_tag_key": f"tag-{index % 5}",
            })
        used = set()
        day = date(2026, 10, 22)
        first = diverse_select(videos, 12, day, "featured", used, "featured")
        second = diverse_select(videos, 12, day, "discovery", used, "discovery")
        first_urls = {video["url"] for video in first}
        second_urls = {video["url"] for video in second}
        self.assertEqual(len(first_urls), 12)
        self.assertEqual(len(second_urls), 12)
        self.assertTrue(first_urls.isdisjoint(second_urls))
        self.assertTrue(all(not any(key.startswith("_") for key in video) for video in first + second))


if __name__ == "__main__":
    unittest.main()
