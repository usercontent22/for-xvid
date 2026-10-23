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

  const playerShell = document.getElementById("detail-player");
  const fullVideo = document.getElementById("detail-hls-player");
  const playerStart = document.getElementById("player-start");
  const playerOverlay = document.getElementById("player-overlay");
  const playerOverlayHeading = document.getElementById("player-overlay-heading");
  const playerOverlayStatus = document.getElementById("player-overlay-status");
  const playerControls = document.getElementById("player-controls");
  const playerToggle = document.getElementById("player-toggle");
  const playerMute = document.getElementById("player-mute");
  const playerVolume = document.getElementById("player-volume");
  const playerFullscreen = document.getElementById("player-fullscreen");
  const playerProgress = document.getElementById("player-progress");
  const playerProgressFill = document.getElementById("player-progress-fill");
  const playerProgressBuffered = document.getElementById("player-progress-buffered");
  const playerProgressThumb = document.getElementById("player-progress-thumb");
  const playerCurrentTime = document.getElementById("player-current-time");
  const playerDuration = document.getElementById("player-duration");
  const seekPreview = document.getElementById("seek-preview");
  const seekPreviewFrame = document.getElementById("seek-preview-frame");
  const seekPreviewTime = document.getElementById("seek-preview-time");

  if (
    playerShell && fullVideo && playerStart && playerOverlay &&
    playerControls && playerToggle && playerProgress
  ) {
    const sourcePath = fullVideo.getAttribute("data-source-path") || "";
    const resolverUrl = fullVideo.getAttribute("data-resolver-url") || "";
    const mosaicUrl = fullVideo.getAttribute("data-mosaic-url") || "";
    let hlsInstance = null;
    let resolving = false;
    let streamReady = false;
    let seekingPointerId = null;
    let lastPreviewTime = 0;
    let lastPointerX = null;
    let previewActive = false;
    let previewImagePromise = null;
    const mosaicPromises = new Map();

    function durationValue() {
      return Number.isFinite(fullVideo.duration) && fullVideo.duration > 0
        ? fullVideo.duration
        : 0;
    }

    function formatPlayerTime(seconds) {
      const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const remainder = total % 60;
      if (hours > 0) {
        return String(hours) + ":" + String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(2, "0");
      }
      return String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(2, "0");
    }

    function setOverlay(heading, message, state) {
      playerOverlayHeading.textContent = heading;
      playerOverlayStatus.textContent = message;
      playerOverlay.classList.toggle("is-loading", state === "loading");
      playerOverlay.classList.toggle("is-error", state === "error");
      playerStart.disabled = state === "loading";
      playerStart.setAttribute("aria-label", state === "error" ? "Try playing the video again" : (streamReady ? "Play video" : "Play full video"));
      const icon = playerStart.querySelector(".player-start-glyph");
      if (icon) {
        icon.innerHTML = state === "loading"
          ? '<span class="player-spinner" aria-hidden="true"></span>'
          : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.8c0-.8.9-1.3 1.6-.8l9.1 6.2a1 1 0 0 1 0 1.6l-9.1 6.2a1 1 0 0 1-1.6-.8z"></path></svg>';
      }
      playerOverlay.hidden = false;
    }

    function updatePlayIcon() {
      const paused = fullVideo.paused || fullVideo.ended;
      const playIcon = playerToggle.querySelector(".icon-play");
      const pauseIcon = playerToggle.querySelector(".icon-pause");
      if (playIcon) playIcon.hidden = !paused;
      if (pauseIcon) pauseIcon.hidden = paused;
      playerToggle.setAttribute("aria-label", paused ? "Play" : "Pause");
    }

    function updateProgress() {
      const duration = durationValue();
      const current = Number.isFinite(fullVideo.currentTime) ? fullVideo.currentTime : 0;
      const fraction = duration ? Math.min(1, Math.max(0, current / duration)) : 0;
      const percent = String(fraction * 100) + "%";
      playerProgressFill.style.width = percent;
      playerProgressThumb.style.left = percent;
      playerProgress.setAttribute("aria-valuemax", String(Math.floor(duration)));
      playerProgress.setAttribute("aria-valuenow", String(Math.floor(current)));
      playerProgress.setAttribute("aria-valuetext", formatPlayerTime(current) + " of " + (duration ? formatPlayerTime(duration) : "unknown duration"));
      playerCurrentTime.textContent = formatPlayerTime(current);
      playerDuration.textContent = duration ? formatPlayerTime(duration) : "--:--";

      let buffered = 0;
      try {
        if (duration && fullVideo.buffered.length) {
          buffered = Math.min(1, fullVideo.buffered.end(fullVideo.buffered.length - 1) / duration);
        }
      } catch (_error) {
        buffered = 0;
      }
      playerProgressBuffered.style.width = String(buffered * 100) + "%";
    }

    function loadMosaicSprite() {
      if (!mosaicUrl) return Promise.resolve(null);
      if (mosaicPromises.has(mosaicUrl)) return mosaicPromises.get(mosaicUrl);

      const promise = new Promise(function (resolve) {
        let parsed;
        try {
          parsed = new URL(mosaicUrl, window.location.href);
          const poster = fullVideo.poster ? new URL(fullVideo.poster, window.location.href) : null;
          if (parsed.protocol !== "https:" || (poster && parsed.origin !== poster.origin)) {
            resolve(null);
            return;
          }
        } catch (_error) {
          resolve(null);
          return;
        }

        const image = new Image();
        image.decoding = "async";
        image.onload = function () {
          if (image.naturalWidth > 0 && image.naturalHeight > 0) {
            resolve({
              image: image,
              width: image.naturalWidth,
              height: image.naturalHeight,
              frameWidth: image.naturalWidth / 10,
              frameHeight: image.naturalHeight / 10
            });
          } else {
            resolve(null);
          }
        };
        image.onerror = function () { resolve(null); };
        image.src = parsed.href;
        if (image.complete && image.naturalWidth > 0) {
          resolve({
            image: image,
            width: image.naturalWidth,
            height: image.naturalHeight,
            frameWidth: image.naturalWidth / 10,
            frameHeight: image.naturalHeight / 10
          });
        }
      });
      mosaicPromises.set(mosaicUrl, promise);
      return promise;
    }

    function positionSeekPreview() {
      if (lastPointerX === null || seekPreview.hidden) return;
      const controlsRect = playerControls.getBoundingClientRect();
      const width = seekPreview.offsetWidth;
      const preferredLeft = lastPointerX - controlsRect.left - width / 2;
      const left = Math.max(8, Math.min(preferredLeft, Math.max(8, controlsRect.width - width - 8)));
      seekPreview.style.left = String(left) + "px";
    }

    async function renderSeekPreview(time) {
      const duration = durationValue();
      if (!duration || !seekPreview || !seekPreviewFrame || !seekPreviewTime) return;
      lastPreviewTime = Math.max(0, Math.min(duration, time));
      seekPreviewTime.textContent = formatPlayerTime(lastPreviewTime);
      seekPreview.hidden = false;
      positionSeekPreview();

      if (!previewImagePromise) previewImagePromise = loadMosaicSprite();
      const sprite = await previewImagePromise;
      if (!previewActive || !sprite) return;

      const frameIndex = Math.min(99, Math.floor((lastPreviewTime / duration) * 100));
      const column = frameIndex % 10;
      const row = Math.floor(frameIndex / 10);
      const displayWidth = Math.min(138, Math.max(94, playerShell.clientWidth * 0.25));
      const scale = displayWidth / sprite.frameWidth;
      const backgroundX = -(column * sprite.frameWidth * scale);
      const backgroundY = -(row * sprite.frameHeight * scale);

      seekPreviewFrame.style.width = String(displayWidth) + "px";
      seekPreviewFrame.style.aspectRatio = String(sprite.frameWidth) + " / " + String(sprite.frameHeight);
      seekPreviewFrame.style.backgroundImage = 'url("' + sprite.image.src.replace(/["\\]/g, "\\$&") + '")';
      seekPreviewFrame.style.backgroundSize = String(sprite.width * scale) + "px " + String(sprite.height * scale) + "px";
      seekPreviewFrame.style.backgroundPosition = String(backgroundX) + "px " + String(backgroundY) + "px";
      positionSeekPreview();
    }

    function hideSeekPreview() {
      previewActive = false;
      lastPointerX = null;
      seekPreview.hidden = true;
    }

    function seekTimeFromPointer(event) {
      const duration = durationValue();
      const rect = playerProgress.getBoundingClientRect();
      if (!duration || rect.width <= 0) return null;
      const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      return fraction * duration;
    }

    function showSeekPreviewFromPointer(event) {
      const time = seekTimeFromPointer(event);
      if (time === null) return;
      previewActive = true;
      lastPointerX = event.clientX;
      renderSeekPreview(time);
    }

    function seekTo(time) {
      const duration = durationValue();
      if (!duration) return;
      fullVideo.currentTime = Math.max(0, Math.min(duration, time));
      updateProgress();
    }

    function setVolumeUi() {
      const muted = fullVideo.muted || fullVideo.volume === 0;
      playerMute.setAttribute("aria-label", muted ? "Unmute" : "Mute");
      const wave = playerMute.querySelector(".volume-wave");
      const mutedMark = playerMute.querySelector(".volume-muted");
      if (wave) wave.hidden = muted;
      if (mutedMark) mutedMark.hidden = !muted;
      playerVolume.value = muted ? "0" : String(fullVideo.volume);
    }

    function clearStream() {
      if (hlsInstance) {
        hlsInstance.destroy();
        hlsInstance = null;
      }
      fullVideo.pause();
      fullVideo.removeAttribute("src");
      fullVideo.querySelectorAll("source").forEach(function (source) { source.remove(); });
      fullVideo.load();
    }

    function failPlayback() {
      resolving = false;
      streamReady = false;
      playerControls.hidden = true;
      clearStream();
      setOverlay("Video unavailable", "We couldn't start playback. Please try again.", "error");
    }

    async function playReadyStream() {
      if (!streamReady) return startPlayback();
      if (fullVideo.ended) {
        try { fullVideo.currentTime = 0; } catch (_error) {}
      }
      try {
        fullVideo.muted = false;
        await fullVideo.play();
      } catch (_error) {
        setOverlay("Your video is ready", "Press play to start watching.", "ready");
        updatePlayIcon();
      }
    }

    async function startPlayback() {
      if (resolving) return;
      if (streamReady) return playReadyStream();
      if (!resolverUrl || !sourcePath) {
        setOverlay("Playback unavailable", "Full playback isn't configured for this video yet.", "error");
        return;
      }

      resolving = true;
      setOverlay("Getting your video ready", "Connecting to the video source…", "loading");

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
        if (!response.ok || !data.hls_url) throw new Error("unavailable");

        const streamUrl = new URL(data.hls_url);
        if (streamUrl.protocol !== "https:" || !/\.m3u8$/i.test(streamUrl.pathname)) {
          throw new Error("unavailable");
        }

        clearStream();
        fullVideo.loop = false;
        fullVideo.muted = false;
        fullVideo.volume = Math.max(0.01, Number(playerVolume.value) || 1);
        fullVideo.controls = false;

        if (window.Hls && window.Hls.isSupported()) {
          hlsInstance = new window.Hls({
            enableWorker: true,
            lowLatencyMode: false,
            backBufferLength: 30
          });
          hlsInstance.on(window.Hls.Events.ERROR, function (_event, info) {
            if (info && info.fatal) failPlayback();
          });
          hlsInstance.on(window.Hls.Events.MANIFEST_PARSED, function () {
            streamReady = true;
            resolving = false;
            playerControls.hidden = false;
            updateProgress();
            setOverlay("Your video is ready", "Starting playback…", "ready");
            playReadyStream();
          });
          hlsInstance.loadSource(streamUrl.href);
          hlsInstance.attachMedia(fullVideo);
        } else if (fullVideo.canPlayType("application/vnd.apple.mpegurl")) {
          fullVideo.src = streamUrl.href;
          fullVideo.load();
          streamReady = true;
          resolving = false;
          playerControls.hidden = false;
          updateProgress();
          setOverlay("Your video is ready", "Starting playback…", "ready");
          playReadyStream();
        } else {
          throw new Error("unsupported");
        }
      } catch (_error) {
        failPlayback();
      }
    }

    playerStart.addEventListener("click", function () {
      if (streamReady) {
        playReadyStream();
      } else {
        startPlayback();
      }
    });

    playerToggle.addEventListener("click", function () {
      if (fullVideo.paused || fullVideo.ended) {
        playReadyStream();
      } else {
        fullVideo.pause();
      }
    });

    fullVideo.addEventListener("click", function () {
      if (!streamReady) return;
      if (fullVideo.paused) playReadyStream();
      else fullVideo.pause();
    });

    fullVideo.addEventListener("playing", function () {
      resolving = false;
      playerOverlay.hidden = true;
      playerControls.hidden = false;
      updatePlayIcon();
      updateProgress();
    });

    fullVideo.addEventListener("pause", function () {
      updatePlayIcon();
      if (streamReady && !fullVideo.ended && !resolving) {
        setOverlay("Video paused", "Press play to continue.", "ready");
      }
    });

    fullVideo.addEventListener("ended", function () {
      updatePlayIcon();
      setOverlay("Watch again", "Replay this video from the beginning.", "ready");
    });

    fullVideo.addEventListener("timeupdate", updateProgress);
    fullVideo.addEventListener("progress", updateProgress);
    fullVideo.addEventListener("durationchange", updateProgress);
    fullVideo.addEventListener("loadedmetadata", updateProgress);

    playerProgress.addEventListener("pointerenter", function (event) {
      if (event.pointerType !== "touch") showSeekPreviewFromPointer(event);
    });
    playerProgress.addEventListener("pointermove", function (event) {
      if (event.pointerType !== "touch" || seekingPointerId !== null) {
        showSeekPreviewFromPointer(event);
      }
    });
    playerProgress.addEventListener("pointerdown", function (event) {
      if (!streamReady || !durationValue()) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      seekingPointerId = event.pointerId;
      playerProgress.setPointerCapture(event.pointerId);
      showSeekPreviewFromPointer(event);
      const time = seekTimeFromPointer(event);
      if (time !== null) seekTo(time);
      event.preventDefault();
    });
    playerProgress.addEventListener("pointerup", function (event) {
      if (seekingPointerId !== event.pointerId) return;
      const time = seekTimeFromPointer(event);
      if (time !== null) seekTo(time);
      seekingPointerId = null;
      try { playerProgress.releasePointerCapture(event.pointerId); } catch (_error) {}
      if (event.pointerType === "touch") hideSeekPreview();
    });
    playerProgress.addEventListener("pointercancel", function () {
      seekingPointerId = null;
      hideSeekPreview();
    });
    playerProgress.addEventListener("pointerleave", function (event) {
      if (seekingPointerId === null && event.pointerType !== "touch") hideSeekPreview();
    });
    playerProgress.addEventListener("keydown", function (event) {
      const duration = durationValue();
      if (!streamReady || !duration) return;
      let target = fullVideo.currentTime;
      if (event.key === "ArrowLeft") target -= 5;
      else if (event.key === "ArrowRight") target += 5;
      else if (event.key === "Home") target = 0;
      else if (event.key === "End") target = duration;
      else if (event.key === "PageDown") target -= 10;
      else if (event.key === "PageUp") target += 10;
      else return;
      event.preventDefault();
      seekTo(target);
    });

    playerMute.addEventListener("click", function () {
      fullVideo.muted = !(fullVideo.muted || fullVideo.volume === 0);
      if (!fullVideo.muted && fullVideo.volume === 0) fullVideo.volume = 1;
      setVolumeUi();
    });
    playerVolume.addEventListener("input", function () {
      const volume = Number(playerVolume.value);
      fullVideo.volume = Number.isFinite(volume) ? Math.max(0, Math.min(1, volume)) : 1;
      fullVideo.muted = fullVideo.volume === 0;
      setVolumeUi();
    });
    fullVideo.addEventListener("volumechange", setVolumeUi);
    setVolumeUi();

    playerFullscreen.addEventListener("click", function () {
      if (document.fullscreenElement === playerShell) {
        if (document.exitFullscreen) document.exitFullscreen();
        return;
      }
      if (fullVideo.webkitEnterFullscreen && /iPhone|iPad|iPod/i.test(navigator.userAgent)) {
        fullVideo.webkitEnterFullscreen();
        return;
      }
      if (playerShell.requestFullscreen) {
        const request = playerShell.requestFullscreen();
        if (request && typeof request.catch === "function") request.catch(function () {});
      }
    });
    document.addEventListener("fullscreenchange", function () {
      const active = document.fullscreenElement === playerShell;
      playerFullscreen.setAttribute("aria-label", active ? "Exit fullscreen" : "Enter fullscreen");
    });

    window.addEventListener("resize", positionSeekPreview);
    if (!resolverUrl) {
      playerStart.disabled = true;
      playerStart.setAttribute("aria-disabled", "true");
      setOverlay("Playback unavailable", "Full playback isn't configured for this video yet.", "error");
    }
  }

})();
