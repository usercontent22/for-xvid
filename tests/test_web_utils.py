import os
import unittest
from unittest.mock import patch

from web.ids import decode_history, decode_token, encode_history, encode_token, normalize_channel_key, slugify
from web.media import format_duration, format_views, image_variant_url, preview_url, thumbnail_url


class RouteKeyTests(unittest.TestCase):
    def test_token_round_trip_for_spaces_and_unicode(self):
        source = "/channel/a space/épisode"
        self.assertEqual(decode_token(encode_token(source)), source)

    def test_slugify_and_channel_key(self):
        self.assertEqual(slugify("Two Words & Studio!"), "two-words-studio")
        self.assertEqual(normalize_channel_key("A B-C!"), "abc")

    def test_invalid_token_is_rejected(self):
        self.assertIsNone(decode_token("not-valid-base64!"))
        self.assertIsNone(decode_token("x" * 5000))

    def test_history_round_trip_and_limit(self):
        self.assertEqual(decode_history(encode_history(["", "abc"])), ["", "abc"])
        self.assertEqual(decode_history(None), [])


class MediaTests(unittest.TestCase):
    def test_thumbnail_base_is_added_to_relative_path(self):
        with patch.dict(os.environ, {"THUMBNAIL_BASE_URL": "https://thumb.example.test"}):
            self.assertEqual(
                thumbnail_url("/asset-id/0/xv_2_t.jpg"),
                "https://thumb.example.test/asset-id/0/xv_2_t.jpg",
            )

    def test_preview_replaces_only_filename_after_zero_directory(self):
        with patch.dict(os.environ, {"THUMBNAIL_BASE_URL": "https://thumb.example.test"}):
            self.assertEqual(
                preview_url("/asset-id/0/xv_2_t.jpg"),
                "https://thumb.example.test/asset-id/0/preview.mp4",
            )
            self.assertEqual(preview_url("/asset-id/not-zero/xv_2_t.jpg"), "")

    def test_separate_preview_host_preserves_path(self):
        with patch.dict(os.environ, {"THUMBNAIL_BASE_URL": "https://thumb.example.test", "PREVIEW_BASE_URL": "https://preview.example.test"}):
            self.assertEqual(
                preview_url("/asset-id/0/xv_2_t.jpg"),
                "https://preview.example.test/asset-id/0/preview.mp4",
            )

    def test_numbered_image_variants(self):
        with patch.dict(os.environ, {"THUMBNAIL_BASE_URL": "https://thumb.example.test"}):
            self.assertEqual(
                image_variant_url("/asset-id/0/xv_22_t.jpg", 3),
                "https://thumb.example.test/asset-id/0/xv_3_t.jpg",
            )
            self.assertEqual(image_variant_url("/asset-id/0/xv_2_t.jpg", 31), "")

    def test_display_formatters(self):
        self.assertEqual(format_views(1000), "1K")
        self.assertEqual(format_views(1_250_000), "1.2M")
        self.assertEqual(format_duration(780), "13:00")
        self.assertEqual(format_duration(3723), "1:02:03")


if __name__ == "__main__":
    unittest.main()
