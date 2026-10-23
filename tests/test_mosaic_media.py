import unittest
from unittest.mock import patch

from web.media import mosaic_url


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

    def test_non_poster_path_returns_empty_string(self):
        self.assertEqual(mosaic_url("/asset-id/thumbnail.jpg"), "")
        self.assertEqual(mosaic_url(None), "")


if __name__ == "__main__":
    unittest.main()
