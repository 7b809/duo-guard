// static/js/totp.js

(function () {
  "use strict";

  var CODES_URL = "/api/totp/codes";
  var entries = {}; // id -> { remaining, period }
  var fetching = false;
  var ticker = null;

  function formatCode(code) {
    if (!code) return "------";
    if (code.length === 6) return code.slice(0, 3) + " " + code.slice(3);
    return code;
  }

  function applyUI() {
    Object.keys(entries).forEach(function (id) {
      var state = entries[id];
      var progressBar = document.querySelector('[data-progress-for="' + id + '"]');
      if (progressBar) {
        var pct = Math.max(0, Math.min(100, (state.remaining / state.period) * 100));
        progressBar.style.width = pct + "%";
      }
      var countdownEl = document.querySelector('[data-countdown-for="' + id + '"]');
      if (countdownEl) {
        countdownEl.textContent = state.remaining + "s";
      }
    });
  }

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
          if (codeEl) {
            codeEl.textContent = formatCode(data.codes[id]);
          }
        });
        Object.keys(data.meta || {}).forEach(function (id) {
          entries[id] = {
            remaining: data.meta[id].remaining,
            period: data.meta[id].period || 30,
          };
        });
        applyUI();
      })
      .catch(function () {
        /* silently ignore */
      })
      .finally(function () {
        fetching = false;
      });
  }

  function tick() {
    var needRefresh = false;
    Object.keys(entries).forEach(function (id) {
      entries[id].remaining -= 1;
      if (entries[id].remaining <= 0) {
        entries[id].remaining = entries[id].period;
        needRefresh = true;
      }
    });
    applyUI();
    if (needRefresh) fetchCodes();
  }

  function init() {
    if (!document.querySelector("[data-code-for]")) return;
    fetchCodes();
    ticker = setInterval(tick, 1000);

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) fetchCodes();
    });
  }

  document.addEventListener("DOMContentLoaded", init);

  // Confirm delete forms
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.matches("form[data-confirm]")) {
      var message = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    }
  });
})();