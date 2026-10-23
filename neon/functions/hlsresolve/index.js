"use strict";

// Public endpoint: fetches only the user's own XVideos catalog path, transforms it
// to the requested videos.<path> route, and extracts an openly exposed HTTPS HLS
// playlist. It does not bypass logins, age gates, captchas, DRM, or other access controls.

const ALLOWED_ORIGINS = new Set([
  "https://for-xvid.vercel.app",
  "http://localhost:5000",
  "http://127.0.0.1:5000",
]);
const PAGE_HOST_ALLOWED = (host) =>
  host === "xvideos.com" || host.endsWith(".xvideos.com");
const PLAYLIST_HOST_ALLOWED = (host) =>
  host === "xvideos.com" ||
  host.endsWith(".xvideos.com") ||
  host === "xvideos-cdn.com" ||
  host.endsWith(".xvideos-cdn.com");

const cache = new Map();
const rateWindows = new Map();
const RATE_LIMIT = 12;
const RATE_WINDOW_MS = 60_000;
const CACHE_TTL_MS = 120_000;
const MAX_HTML_CHARS = 8 * 1024 * 1024;

function json(request, value, status = 200) {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Accept, Content-Type",
    "Access-Control-Max-Age": "600",
  };
  const origin = request.headers.get("origin");
  if (origin && ALLOWED_ORIGINS.has(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Vary"] = "Origin";
  }
  return new Response(JSON.stringify(value), { status, headers });
}

function clientKey(request) {
  // Only used for best-effort per-isolate throttling; never trusted for authorization.
  return (request.headers.get("x-forwarded-for") || "unknown").split(",")[0].trim().slice(0, 80);
}

function throttled(request) {
  const now = Date.now();
  const key = clientKey(request);
  let state = rateWindows.get(key);
  if (!state || now - state.start >= RATE_WINDOW_MS) {
    state = { start: now, count: 0 };
  }
  state.count += 1;
  rateWindows.set(key, state);

  if (rateWindows.size > 2000) {
    for (const [ip, entry] of rateWindows) {
      if (now - entry.start > RATE_WINDOW_MS) rateWindows.delete(ip);
    }
  }
  return state.count > RATE_LIMIT;
}

function targetFromPath(path) {
  if (
    typeof path !== "string" ||
    path.length > 300 ||
    !/^\/[A-Za-z0-9_-]{6,32}\/[A-Za-z0-9._-]{1,240}$/.test(path) ||
    path.includes("..")
  ) {
    return null;
  }

  // Example: /aaabd271c/my_video -> https://www.xvideos.com/videos.aaabd271c/my_video
  return new URL("https://www.xvideos.com/videos" + path.replace("/", "."));
}

function normalizePageText(html) {
  return html
    .replace(/\\u0026/gi, "&")
    .replace(/&amp;/gi, "&")
    .replace(/\\u003a/gi, ":")
    .replace(/\\u003f/gi, "?")
    .replace(/\\\//g, "/")
    .replace(/\\x3a/gi, ":")
    .replace(/\\x2f/gi, "/");
}

function findPlaylist(html) {
  const normalized = normalizePageText(html);
  const candidates =
    normalized.match(/https?:\/\/[^\s"'<>\\]+?\.m3u8(?:\?[^\s"'<>\\]*)?/gi) || [];

  for (const candidate of candidates) {
    try {
      const playlist = new URL(candidate.replace(/[),;]+$/, ""));
      if (
        playlist.protocol === "https:" &&
        !playlist.username &&
        !playlist.password &&
        /\.m3u8$/i.test(playlist.pathname) &&
        PLAYLIST_HOST_ALLOWED(playlist.hostname.toLowerCase())
      ) {
        return playlist.href;
      }
    } catch (_error) {
      // Continue past malformed URL candidates found in page scripts.
    }
  }
  return null;
}

async function fetchSourcePage(initialUrl) {
  let current = initialUrl;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    const response = await fetch(current.href, {
      method: "GET",
      redirect: "manual",
      signal: AbortSignal.timeout(12_000),
      headers: {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (compatible; XWorldStreamResolver/1.0)",
        "Referer": "https://www.xvideos.com/",
      },
    });

    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get("location");
      if (!location) throw new Error("Source returned a redirect without a location.");
      const next = new URL(location, current);
      if (next.protocol !== "https:" || !PAGE_HOST_ALLOWED(next.hostname.toLowerCase())) {
        throw new Error("Source redirected outside the permitted XVideos page domain.");
      }
      current = next;
      continue;
    }

    if (!response.ok) {
      throw new Error("Source page returned HTTP " + response.status + ".");
    }

    const contentType = response.headers.get("content-type") || "";
    const declaredLength = Number(response.headers.get("content-length") || 0);
    if (declaredLength > MAX_HTML_CHARS) {
      throw new Error("Source page response was too large.");
    }
    if (contentType && !/html|text\/plain|javascript/i.test(contentType)) {
      throw new Error("Source did not return a readable page.");
    }

    const html = await response.text();
    if (html.length > MAX_HTML_CHARS) throw new Error("Source page response was too large.");
    return html;
  }
  throw new Error("Source page redirected too many times.");
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      const origin = request.headers.get("origin");
      if (origin && !ALLOWED_ORIGINS.has(origin)) {
        return json(request, { error: "Origin not allowed." }, 403);
      }
      const headers = {
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Accept, Content-Type",
        "Access-Control-Max-Age": "600",
      };
      if (origin) {
        headers["Access-Control-Allow-Origin"] = origin;
        headers["Vary"] = "Origin";
      }
      return new Response(null, { status: 204, headers });
    }

    if (request.method !== "GET") {
      return json(request, { error: "Use GET for stream resolution." }, 405);
    }

    const origin = request.headers.get("origin");
    if (origin && !ALLOWED_ORIGINS.has(origin)) {
      return json(request, { error: "Origin not allowed." }, 403);
    }
    if (throttled(request)) {
      return json(request, { error: "Too many requests. Try again shortly." }, 429);
    }

    const target = targetFromPath(url.searchParams.get("path"));
    if (!target) {
      return json(request, { error: "A valid catalog video path is required." }, 400);
    }

    const cached = cache.get(target.pathname);
    if (cached && cached.expiresAt > Date.now()) {
      return json(request, { hls_url: cached.hlsUrl, page_url: target.href, cached: true });
    }

    try {
      const html = await fetchSourcePage(target);
      const hlsUrl = findPlaylist(html);
      if (!hlsUrl) {
        return json(
          request,
          { error: "No public HLS playlist was found on the source page.", page_url: target.href },
          404,
        );
      }

      cache.set(target.pathname, { hlsUrl, expiresAt: Date.now() + CACHE_TTL_MS });
      if (cache.size > 300) {
        const firstKey = cache.keys().next().value;
        if (firstKey) cache.delete(firstKey);
      }
      return json(request, { hls_url: hlsUrl, page_url: target.href, cached: false });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Source resolution failed.";
      return json(request, { error: message }, 502);
    }
  },
};
