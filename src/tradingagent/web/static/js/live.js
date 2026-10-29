/* NexaGold dashboard : live updates, progressive enhancement only.
   Every page is complete and correct without this file. It listens to the change marker the
   server pushes (/events?ticks=1) and, when the agent has written something, reloads the page
   in place: same URL, same scroll position. It never reloads while the operator is mid-gesture
   (a dialog is open, a field has focus, text is selected), and the topbar button pauses it. */
(function () {
  "use strict";
  if (!window.EventSource || document.documentElement.dataset.live === "off") { return; }
  document.documentElement.setAttribute("data-live-ready", "");

  var KEY = "ta-live";
  var DEBOUNCE_MS = 400;
  var button = document.getElementById("live-toggle");
  var label = button ? button.querySelector(".live-label") : null;
  var source = null;
  var version = null;
  var pending = false;
  var timer = 0;

  function enabled() {
    try { return localStorage.getItem(KEY) !== "off"; } catch (error) { return true; }
  }
  function setState(state, text) {
    if (!button) { return; }
    button.dataset.state = state;
    button.setAttribute("aria-pressed", String(state !== "paused"));
    button.setAttribute("title", text);
    if (label) { label.textContent = state === "ok" ? "Direct" : (state === "paused" ? "Pause" : "…"); }
  }
  function busy() {
    if (document.hidden) { return true; }
    if (document.querySelector("dialog[open]")) { return true; }
    if (document.body.dataset.drawer === "open") { return true; }
    var active = document.activeElement;
    if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) { return true; }
    var selection = window.getSelection && window.getSelection();
    return Boolean(selection && !selection.isCollapsed);
  }
  function refresh() {
    window.clearTimeout(timer);
    if (!pending) { return; }
    if (busy()) { timer = window.setTimeout(refresh, 1500); return; }
    pending = false;
    window.location.reload();
  }
  function onTick(message) {
    var payload;
    try { payload = JSON.parse(message.data); } catch (error) { return; }
    if (version !== null && payload.version !== version) {
      pending = true;
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, DEBOUNCE_MS);
    }
    version = payload.version;
  }
  function start() {
    if (source) { return; }
    setState("wait", "Connexion au flux en direct");
    source = new EventSource("/events?ticks=1");
    source.addEventListener("open", function () { setState("ok", "Mise à jour en direct active"); });
    source.addEventListener("error", function () { setState("wait", "Flux interrompu, reconnexion"); });
    source.addEventListener("tick", onTick);
  }
  function stop() {
    if (source) { source.close(); source = null; }
    window.clearTimeout(timer);
    pending = false;
    version = null;
  }

  if (button) {
    button.addEventListener("click", function () {
      var on = enabled();
      try { localStorage.setItem(KEY, on ? "off" : "on"); } catch (error) { /* private mode */ }
      if (on) { stop(); setState("paused", "Mise à jour en direct en pause"); } else { start(); }
    });
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden && pending) { refresh(); } });
  if (enabled()) { start(); } else { setState("paused", "Mise à jour en direct en pause"); }
})();
