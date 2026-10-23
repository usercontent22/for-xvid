import json
import unittest
from unittest.mock import patch

from web.seo import (
    build_page_seo,
    canonical_path_for_request,
    iso_duration,
    named_item_list_schema,
    video_item_list_schema,
    video_object,
)


class StructuredDataTests(unittest.TestCase):
    def setUp(self):
        self.site_env = patch.dict("os.environ", {"SITE_URL": "https://www.example.test"})
        self.site_env.start()
        self.addCleanup(self.site_env.stop)

    def test_global_graph_has_organization_website_search_and_canonical_page(self):
        seo = build_page_seo(
            title="Popular videos — XXX World",
            description="Browse popular videos.",
            canonical_path="/browse?section=popular",
            page_type="CollectionPage",
            breadcrumbs=[
                ("Home", "/"),
                ("Videos", "/browse?section=popular"),
                ("Popular videos", "/browse?section=popular"),
            ],
        )
        graph = seo["structured_data"]["@graph"]
        types = {node.get("@type"): node for node in graph if isinstance(node, dict)}
        self.assertEqual(seo["canonical_url"], "https://www.example.test/browse?section=popular")
        self.assertEqual(seo["robots"], "index,follow,max-image-preview:large")
        self.assertIn("Organization", types)
        self.assertIn("WebSite", types)
        self.assertEqual(types["WebSite"]["potentialAction"]["@type"], "SearchAction")
        self.assertIn("BreadcrumbList", types)
        self.assertEqual(types["CollectionPage"]["url"], seo["canonical_url"])
        json.dumps(seo["structured_data"])

    def test_video_object_contains_only_known_metadata(self):
        video = {
            "title": "He couldn't get a girlfriend",
            "video_href": "/video/example--token",
            "thumbnail_url": "https://thumb.example.test/example.jpg",
            "duration_seconds": 3723,
            "views": 1800,
            "tags": ["comedy", "short film"],
        }
        schema = video_object(video)
        self.assertEqual(schema["@type"], "VideoObject")
        self.assertEqual(schema["name"], "He couldn't get a girlfriend")
        self.assertEqual(schema["thumbnailUrl"], ["https://thumb.example.test/example.jpg"])
        self.assertEqual(schema["duration"], "PT1H2M3S")
        self.assertEqual(schema["interactionStatistic"]["userInteractionCount"], 1800)
        # Never invent source upload dates: the live database query doesn't expose one yet.
        self.assertNotIn("uploadDate", schema)
        self.assertNotIn("contentUrl", schema)

    def test_video_list_has_positions_urls_and_thumbnails(self):
        items = [
            {
                "title": "Video One",
                "video_href": "/video/one--abc",
                "thumbnail_url": "https://thumb.example.test/one.jpg",
            },
            {"title": "Video Two", "video_href": "/video/two--def"},
        ]
        schema = video_item_list_schema(items, "Popular videos", "/", fragment="popular-list")
        self.assertEqual(schema["@id"], "https://www.example.test/#popular-list")
        self.assertEqual(schema["numberOfItems"], 2)
        self.assertEqual(schema["itemListElement"][0]["position"], 1)
        self.assertEqual(schema["itemListElement"][0]["item"]["url"], "https://www.example.test/video/one--abc")
        self.assertIn("thumbnailUrl", schema["itemListElement"][0]["item"])
        self.assertNotIn("thumbnailUrl", schema["itemListElement"][1]["item"])

    def test_category_and_gallery_lists_share_supported_list_markup(self):
        categories = named_item_list_schema(
            [{"name": "Comedy", "url": "/category/comedy--token"}],
            "Video categories",
            "/",
            fragment="categories-list",
        )
        self.assertEqual(categories["itemListElement"][0]["item"]["name"], "Comedy")
        images = named_item_list_schema(
            [{"name": "Frame 1", "url": "https://thumb.example.test/frame.jpg", "image": True}],
            "Image frames",
            "/images/example--token",
            fragment="frames-list",
        )
        self.assertEqual(images["itemListElement"][0]["item"]["@type"], "ImageObject")
        self.assertEqual(iso_duration(0), None)
        self.assertEqual(iso_duration(65), "PT1M5S")

    def test_canonical_path_filters_tracking_and_navigation_history(self):
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context("/browse?section=popular&utm_source=mail&history=previous"):
            self.assertEqual(
                canonical_path_for_request(),
                "/browse?section=popular",
            )


if __name__ == "__main__":
    unittest.main()
