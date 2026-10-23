(function () {
  "use strict";

  const menuButton = document.querySelector(".mobile-menu-toggle");
  const nav = document.querySelector(".primary-nav");
  if (menuButton && nav) {
    menuButton.addEventListener("click", function () {
      const expanded = menuButton.getAttribute("aria-expanded") === "true";
      menuButton.setAttribute("aria-expanded", String(!expanded));
      nav.classList.toggle("is-open", !expanded);
    });
  }

  const ageGate = document.getElementById("age-gate");
  const confirmAge = document.getElementById("age-confirm");
  if (ageGate && confirmAge) {
    let accepted = false;
    try {
      accepted = window.localStorage.getItem("xxxworld-adult-confirmed") === "yes";
    } catch (_error) {
      accepted = false;
    }
    if (!accepted) {
      ageGate.hidden = false;
      document.body.classList.add("age-gate-open");
    }
    confirmAge.addEventListener("click", function () {
      try {
        window.localStorage.setItem("xxxworld-adult-confirmed", "yes");
      } catch (_error) {
        // The notice can still be dismissed when browser storage is unavailable.
      }
      ageGate.hidden = true;
      document.body.classList.remove("age-gate-open");
    });
  }

  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reducedMotion && !("ontouchstart" in window)) {
    document.querySelectorAll(".video-card__media[data-preview]").forEach(function (card) {
      const video = card.querySelector(".video-card__preview");
      if (!video) return;
      const sourceUrl = card.getAttribute("data-preview");
      let timer = null;

      function stopPreview() {
        if (timer) {
          window.clearTimeout(timer);
          timer = null;
        }
        video.pause();
        video.removeAttribute("src");
        video.load();
        card.classList.remove("is-previewing");
      }

      card.addEventListener("pointerenter", function (event) {
        if (event.pointerType === "touch") return;
        timer = window.setTimeout(function () {
          video.src = sourceUrl;
          const result = video.play();
          if (result && typeof result.catch === "function") result.catch(function () {});
          card.classList.add("is-previewing");
        }, 420);
      });
      card.addEventListener("pointerleave", stopPreview);
      card.addEventListener("focusout", function (event) {
        if (!card.contains(event.relatedTarget)) stopPreview();
      });
    });
  }

  const fullVideo = document.getElementById("detail-hls-player");
  const fullVideoButton = document.getElementById("load-full-video");
  const fullVideoStatus = document.getElementById("full-player-status");

  if (fullVideo && fullVideoButton && fullVideoStatus) {
    const sourcePath = fullVideo.getAttribute("data-source-path") || "";
    const resolverUrl = fullVideo.getAttribute("data-resolver-url") || "";
    const previewUrl = (fullVideo.querySelector("source") || {}).src || "";
    let hlsInstance = null;
    let resolving = false;

    function setPlaybackStatus(message, isError) {
      fullVideoStatus.textContent = message;
      fullVideoStatus.classList.toggle("is-error", Boolean(isError));
    }

    fullVideoButton.addEventListener("click", async function () {
      if (resolving) return;
      if (!resolverUrl || !sourcePath) {
        setPlaybackStatus("Full playback is not configured for this video.", true);
        return;
      }

      resolving = true;
      fullVideoButton.disabled = true;
      fullVideoButton.textContent = "Loading stream…";
      setPlaybackStatus("Finding the HLS playlist…", false);

      try {
        const endpoint = new URL(resolverUrl);
        endpoint.searchParams.set("path", sourcePath);
        const response = await fetch(endpoint.toString(), {
          method: "GET",
          headers: { "Accept": "application/json" },
          credentials: "omit",
          cache: "no-store"
        });
        const data = await response.json().catch(function () { return {}; });
        if (!response.ok || !data.hls_url) {
          throw new Error(data.error || "The source did not provide a playable HLS stream.");
        }

        const streamUrl = new URL(data.hls_url);
        if (streamUrl.protocol !== "https:" || !/\.m3u8$/i.test(streamUrl.pathname)) {
          throw new Error("The resolver returned an invalid playlist URL.");
        }

        if (hlsInstance) {
          hlsInstance.destroy();
          hlsInstance = null;
        }
        fullVideo.pause();
        fullVideo.removeAttribute("src");
        fullVideo.querySelectorAll("source").forEach(function (source) { source.remove(); });
        fullVideo.loop = false;
        fullVideo.muted = false;
        fullVideo.load();

        if (window.Hls && window.Hls.isSupported()) {
          hlsInstance = new window.Hls({
            enableWorker: true,
            lowLatencyMode: false,
            backBufferLength: 30
          });
          hlsInstance.on(window.Hls.Events.ERROR, function (_event, info) {
            if (info && info.fatal) {
              setPlaybackStatus("HLS playback failed. The source may block cross-origin playback or the stream may have expired.", true);
              fullVideoButton.disabled = false;
              fullVideoButton.textContent = "Retry full video";
            }
          });
          hlsInstance.loadSource(streamUrl.href);
          hlsInstance.attachMedia(fullVideo);
          hlsInstance.on(window.Hls.Events.MANIFEST_PARSED, function () {
            const attempt = fullVideo.play();
            if (attempt && typeof attempt.catch === "function") {
              attempt.catch(function () { setPlaybackStatus("Stream ready — press play in the player.", false); });
            }
          });
        } else if (fullVideo.canPlayType("application/vnd.apple.mpegurl")) {
          fullVideo.src = streamUrl.href;
          fullVideo.load();
          const attempt = fullVideo.play();
          if (attempt && typeof attempt.catch === "function") {
            attempt.catch(function () { setPlaybackStatus("Stream ready — press play in the player.", false); });
          }
        } else {
          throw new Error("This browser does not support HLS playback.");
        }

        setPlaybackStatus("HLS stream found. Loading video…", false);
        fullVideoButton.textContent = "Reload full video";
      } catch (error) {
        setPlaybackStatus(error && error.message ? error.message : "Unable to load the full video.", true);
        fullVideoButton.disabled = false;
        fullVideoButton.textContent = "Retry full video";
        if (!fullVideo.src && previewUrl && !fullVideo.querySelector("source")) {
          const source = document.createElement("source");
          source.src = previewUrl;
          source.type = "video/mp4";
          fullVideo.appendChild(source);
          fullVideo.load();
          fullVideo.muted = true;
          fullVideo.loop = true;
        }
      } finally {
        resolving = false;
        if (fullVideoButton.textContent !== "Loading stream…") {
          fullVideoButton.disabled = false;
        }
      }
    });
  }

})();
