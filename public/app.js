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
})();
