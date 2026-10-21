import unittest

from scripts.scrape_video_tags import build_request_url, extract_tags, normalize_video_url


class VideoTagExtractionTests(unittest.TestCase):
    def test_extracts_video_tag_and_keywords_meta(self):
        html = """
        <html><head>
          <meta property="video:tag" content="red, green">
          <meta property="video:tag" content="blue">
          <meta name="keywords" content="green, yellow, red">
          <meta name="keywords" content="  ">
        </head><body><p>body-only words are ignored</p></body></html>
        """
        self.assertEqual(
            extract_tags(html),
            ["blue", "green", "red", "yellow"],
        )

    def test_empty_meta_tags_return_empty_list(self):
        self.assertEqual(extract_tags("<html><head></head><body>text</body></html>"), [])

    def test_relative_video_url_uses_configured_source_domain(self):
        self.assertEqual(
            normalize_video_url("/channel/video-slug"),
            "https://www.xvideos.com/channel/video-slug",
        )

    def test_request_url_adds_video_prefix_to_video_id_path(self):
        self.assertEqual(
            build_request_url(
                "https://www.xvideos.com/aalcci2892/naughty_babes_aj_applegate_and_brookl"
            ),
            "https://www.xvideos.com/video.aalcci2892/naughty_babes_aj_applegate_and_brookl",
        )

    def test_request_url_transforms_relative_url_and_preserves_query(self):
        self.assertEqual(
            build_request_url("/aalcci2892/video-slug?quality=high"),
            "https://www.xvideos.com/video.aalcci2892/video-slug?quality=high",
        )

    def test_request_url_does_not_double_prefix(self):
        self.assertEqual(
            build_request_url("https://www.xvideos.com/video.aalcci2892/video-slug"),
            "https://www.xvideos.com/video.aalcci2892/video-slug",
        )


if __name__ == "__main__":
    unittest.main()
