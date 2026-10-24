import unittest

from app import app
from web.routes.browse import _cursor_page_items


class WebSmokeTests(unittest.TestCase):
    def test_all_templates_compile(self):
        # Catches duplicate Jinja blocks and missing template syntax before deployment.
        for template_name in app.jinja_env.list_templates():
            app.jinja_env.get_template(template_name)

    def test_homepage_renders_without_database_credentials(self):
        # The snapshot repository degrades gracefully when local DB env vars are absent.
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"XXX World", response.data)

    def test_collection_pages_use_catalog_redesign_shell(self):
        client = app.test_client()
        response = client.get("/browse?section=popular")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'catalog-redesign', response.data)
        self.assertIn(b'collection-switcher', response.data)

    def test_cursor_pagination_exposes_recent_page_numbers_without_catalog_counts(self):
        history = [""] + [f"cursor{page}" for page in range(2, 10)]
        with app.test_request_context("/browse?section=popular"):
            items, previous_url, next_url = _cursor_page_items(
                "browse.listing",
                {"section": "popular"},
                page=10,
                cursor="cursor10",
                history=history,
                next_cursor="cursor11",
            )
        numbered = {item["number"]: item for item in items if "number" in item}
        self.assertTrue({1, 7, 8, 9, 10, 11}.issubset(numbered))
        self.assertIn("cursor=cursor7", numbered[7]["url"])
        self.assertIsNone(numbered[10]["url"])
        self.assertIsNotNone(previous_url)
        self.assertIsNotNone(next_url)

    def test_health_endpoint_does_not_expose_connection_strings(self):
        client = app.test_client()
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"DATABASE_URL_1=", response.data)
        self.assertNotIn(b"DATABASE_URL_2=", response.data)

    def test_asset_routes(self):
        client = app.test_client()
        self.assertEqual(client.get("/styles.css").status_code, 200)
        self.assertEqual(client.get("/app.js").status_code, 200)
        self.assertEqual(client.get("/channel.css").status_code, 200)


if __name__ == "__main__":
    unittest.main()
