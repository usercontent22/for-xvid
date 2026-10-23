import unittest

from web.media import decorate_video


class VideoTitleTests(unittest.TestCase):
    def test_decodes_html_entities_in_title(self):
        video = decorate_video({
            "url": "https://example.test/video/123",
            "title": "He couldn&#039;t get a girlf&#x20;",
        })
        self.assertEqual(video["title"], "He couldn't get a girlf")

    def test_decodes_double_encoded_apostrophe(self):
        video = decorate_video({
            "url": "https://example.test/video/123",
            "title": "He couldn&amp;#039;t get a girlfriend",
        })
        self.assertEqual(video["title"], "He couldn't get a girlfriend")

    def test_missing_title_gets_fallback(self):
        video = decorate_video({"url": "https://example.test/video/123"})
        self.assertEqual(video["title"], "Untitled video")


if __name__ == "__main__":
    unittest.main()
