"""XXX World Flask entrypoint for Vercel."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from web.ids import encode_token, slugify
from web.media import format_views
from web.routes.browse import browse
from web.routes.home import home
from web.routes.videos import videos

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING").upper())

app = Flask(__name__, template_folder="templates", static_folder=None)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or os.urandom(32),
    TEMPLATES_AUTO_RELOAD=False,
    MAX_CONTENT_LENGTH=1024 * 1024,
)
app.register_blueprint(home)
app.register_blueprint(browse)
app.register_blueprint(videos)


# Vercel serves public/ files directly from its CDN. These routes support local Flask runs.
@app.get("/styles.css")
def local_stylesheet():
    return send_from_directory(Path(__file__).parent / "public", "styles.css", max_age=3600)


@app.get("/channel.css")
def local_channel_stylesheet():
    return send_from_directory(Path(__file__).parent / "public", "channel.css", max_age=3600)


@app.get("/app.js")
def local_javascript():
    return send_from_directory(Path(__file__).parent / "public", "app.js", max_age=3600)


@app.context_processor
def inject_template_context():
    return {
        "static_assets_base_url": os.getenv("STATIC_ASSETS_BASE_URL", "").strip().rstrip("/"),
        "hls_resolver_url": os.getenv("HLS_RESOLVER_URL", "").strip().rstrip("/"),
        "current_year": datetime.now(timezone.utc).year,
    }


# Jinja filters used for tokenized links and compact metadata display.
app.jinja_env.filters["slugify"] = slugify
app.jinja_env.filters["tokenize"] = encode_token
app.jinja_env.filters["views"] = format_views


@app.get("/healthz")
def healthz():
    return jsonify(
        ok=True,
        app="xxxworld",
        database_configured=bool(os.getenv("DATABASE_URL_3") and os.getenv("DATABASE_URL_4")),
        catalog_configured=bool(os.getenv("DATABASE_URL_3") and os.getenv("DATABASE_URL_4")),
        snapshot_database_configured=bool(os.getenv("DATABASE_URL_5")),
        thumbnail_domain_configured=bool(
            os.getenv("THUMBNAIL_BASE_URL") or os.getenv("IMAGE_BASE_URL")
        ),
        static_assets_configured=bool(os.getenv("STATIC_ASSETS_BASE_URL")),
    )


@app.after_request
def response_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    if request.method == "GET" and request.path != "/healthz":
        if request.path == "/":
            response.headers.setdefault("Cache-Control", "public, max-age=0, s-maxage=120, stale-while-revalidate=600")
        elif request.path.startswith("/images/"):
            response.headers.setdefault("Cache-Control", "public, max-age=0, s-maxage=86400, stale-while-revalidate=604800")
        elif request.path.startswith("/video/"):
            response.headers.setdefault("Cache-Control", "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400")
        elif request.path.startswith("/search"):
            response.headers.setdefault("Cache-Control", "public, max-age=0, s-maxage=300, stale-while-revalidate=900")
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=0, s-maxage=600, stale-while-revalidate=3600")
    else:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(error):
    logging.exception("Unhandled web request error: %s", error)
    return render_template("404.html", server_error=True), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
