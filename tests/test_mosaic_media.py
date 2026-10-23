import unittest
from unittest.mock import patch

from web.media import mosaic_url, preview_url, thumbnail_url


class MosaicUrlTests(unittest.TestCase):
    def test_absolute_poster_maps_to_mosaic_on_same_host(self):
        poster = "https://thumb-cdn77.xvideos-cdn.com/3ca757e2-de36-45e4-bfdf-432d806b5bd5/0/xv_29_t.jpg"
        self.assertEqual(
            mosaic_url(poster),
            "https://thumb-cdn77.xvideos-cdn.com/3ca757e2-de36-45e4-bfdf-432d806b5bd5/0/mozaique_full.jpg",
        )

    def test_relative_poster_uses_configured_thumbnail_base(self):
        with patch.dict("os.environ", {"THUMBNAIL_BASE_URL": "https://thumb.example.test/"}):
            self.assertEqual(
                mosaic_url("/asset-id/0/xv_2_t.jpg"),
                "https://thumb.example.test/asset-id/0/mozaique_full.jpg",
            )

    def test_relative_thumbnail_strips_erroneous_g_prefix(self):
        with patch.dict("os.environ", {"THUMBNAIL_BASE_URL": "https://thumb-cdn77.xvideos-cdn.com/"}):
            self.assertEqual(
                thumbnail_url("g/e9409c2e-c32f-446f-b066-c1efd47ad641/0/xv_2_t.jpg"),
                "https://thumb-cdn77.xvideos-cdn.com/e9409c2e-c32f-446f-b066-c1efd47ad641/0/xv_2_t.jpg",
            )

    def test_absolute_thumbnail_strips_erroneous_g_prefix(self):
        self.assertEqual(
            thumbnail_url("https://thumb-cdn77.xvideos-cdn.com/g/e9409c2e-c32f-446f-b066-c1efd47ad641/0/xv_2_t.jpg"),
            "https://thumb-cdn77.xvideos-cdn.com/e9409c2e-c32f-446f-b066-c1efd47ad641/0/xv_2_t.jpg",
        )

    def test_preview_and_mosaic_strip_erroneous_g_prefix(self):
        path = "g/e9409c2e-c32f-446f-b066-c1efd47ad641/0/xv_2_t.jpg"
        expected_base = "https://thumb-cdn77.xvideos-cdn.com/e9409c2e-c32f-446f-b066-c1efd47ad641/0/"
        with patch.dict("os.environ", {"THUMBNAIL_BASE_URL": "https://thumb-cdn77.xvideos-cdn.com/"}):
            self.assertEqual(preview_url(path), expected_base + "preview.mp4")
            self.assertEqual(mosaic_url(path), expected_base + "mozaique_full.jpg")

    def test_non_poster_path_returns_empty_string(self):
        self.assertEqual(mosaic_url("/asset-id/thumbnail.jpg"), "")
        self.assertEqual(mosaic_url(None), "")


if __name__ == "__main__":
    unittest.main()
