// static/js/totp.js

(function () {
  "use strict";

  var CODES_URL = "/api/totp/codes";
  var entries = {};       // id -> { remaining, period }
  var revealed = {};      // id -> true/false
  var fetching = false;

  /* -------------------- helpers -------------------- */

  function formatCode(code) {
    if (!code) return "------";
    if (code.length === 6) return code.slice(0, 3) + " " + code.slice(3);
    return code;
  }

  function setRevealed(id, on) {
    revealed[id] = !!on;

    var codeEl = document.querySelector('[data-code-for="' + id + '"]');
    if (codeEl) codeEl.classList.toggle("revealed", !!on);

    var eyeEl = document.querySelector('[data-eye-for="' + id + '"]');
    if (eyeEl) {
      eyeEl.className = (on ? "bi bi-eye-slash" : "bi bi-eye") + " totp-eye";
    }

    var wrap = document.querySelector('[data-reveal-for="' + id + '"]');
    if (wrap) {
      wrap.setAttribute(
        "aria-label",
        (on ? "Hide" : "Reveal") + " TOTP code"
      );
    }
  }

  /* -------------------- data fetching -------------------- */

  function fetchCodes() {
    if (fetching) return;
    fetching = true;

    fetch(CODES_URL, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        Object.keys(data.codes || {}).forEach(function (id) {
          var codeEl = document.querySelector('[data-code-for="' + id + '"]');
          if (codeEl) codeEl.textContent = formatCode(data.codes[id]);
        });
        Object.keys(data.meta || {}).forEach(function (id) {
          entries[id] = {
            remaining: data.meta[id].remaining,
            period: data.meta[id].period || 30,
          };
          // Ensure reveal state map has every entry
          if (!(id in revealed)) revealed[id] = false;
        });
        applyProgress();
      })
      .catch(function () { /* silently ignore */ })
      .finally(function () { fetching = false; });
  }

  /* -------------------- progress / countdown -------------------- */

  function applyProgress() {
    Object.keys(entries).forEach(function (id) {
      var state = entries[id];
      var bar = document.querySelector('[data-progress-for="' + id + '"]');
      if (bar) {
        var pct = Math.max(0, Math.min(100, (state.remaining / state.period) * 100));
        bar.style.width = pct + "%";
      }
      var cd = document.querySelector('[data-countdown-for="' + id + '"]');
      if (cd) cd.textContent = state.remaining + "s";
    });
  }

  function tick() {
    var needRefresh = false;
    Object.keys(entries).forEach(function (id) {
      entries[id].remaining -= 1;
      if (entries[id].remaining <= 0) {
        entries[id].remaining = entries[id].period;
        // Professional touch: auto-hide when the code rotates so a revealed
        // code is always the *current* one — never a stale value on screen.
        if (revealed[id]) setRevealed(id, false);
        needRefresh = true;
      }
    });
    applyProgress();
    if (needRefresh) fetchCodes();
  }

  /* -------------------- reveal interactions -------------------- */

  function toggleFromEvent(target) {
    var wrap = target.closest ? target.closest("[data-reveal-for]") : null;
    if (!wrap) return false;
    var id = wrap.getAttribute("data-reveal-for");
    setRevealed(id, !revealed[id]);
    return true;
  }

  document.addEventListener("click", function (e) {
    // Don't hijack clicks on links, buttons, or forms inside the card
    if (e.target.closest("button, a, input, form")) return;
    toggleFromEvent(e.target);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var focused = document.activeElement;
    if (!focused || !focused.matches("[data-reveal-for]")) return;
    e.preventDefault();
    toggleFromEvent(focused);
  });

  /* -------------------- confirm-dialogs on delete / regenerate / revoke -------------------- */

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.matches("form[data-confirm]")) {
      var msg = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(msg)) e.preventDefault();
    }
  });

  /* -------------------- boot -------------------- */

  function init() {
    if (!document.querySelector("[data-code-for]")) return;
    fetchCodes();
    setInterval(tick, 1000);

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) fetchCodes();
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();