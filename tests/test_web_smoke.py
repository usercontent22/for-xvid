import unittest

from app import app


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
