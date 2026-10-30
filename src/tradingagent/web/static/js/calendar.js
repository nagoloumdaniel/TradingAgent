/* Calendrier économique : amélioration progressive uniquement.
   La page est complète et correcte sans ce fichier : chaque jour est un vrai lien
   (?day=YYYY-MM-DD, ?month=YYYY-MM). Ici on ajoute trois choses, et rien d'autre :
     1. les comptes à rebours (« dans 12 min ») avancent sans recharger ;
     2. un clic sur un jour ou sur la navigation du mois remplace le panneau du jour et la
        grille sans recharger la page entière (history.pushState, focus géré) ;
     3. les flèches déplacent le focus dans la grille du mois.
   Si le fetch échoue, ou si la réponse n'a pas la forme attendue, on laisse le navigateur
   faire une navigation normale : jamais d'état intermédiaire cassé. */
(function () {
  "use strict";

  var REGIONS = ["day-panel", "month-grid", "week-strip"];

  /* -- 1. comptes à rebours ---------------------------------------------------------- */
  function words(ms) {
    var s = Math.round(ms / 1000);
    if (s < 90) { return "dans " + Math.max(s, 1) + " s"; }
    var m = Math.round(s / 60);
    if (m < 90) { return "dans " + m + " min"; }
    var h = Math.floor(m / 60);
    var rest = m % 60;
    if (h < 24) { return "dans " + h + " h " + (rest < 10 ? "0" : "") + rest; }
    return "dans " + Math.floor(h / 24) + " j " + (h % 24) + " h";
  }
  function tick() {
    var now = Date.now();
    var nodes = document.querySelectorAll("[data-countdown]");
    for (var i = 0; i < nodes.length; i += 1) {
      var node = nodes[i];
      var at = Date.parse(node.getAttribute("data-countdown"));
      if (isNaN(at)) { continue; }
      var left = at - now;
      if (left > 0) {
        node.textContent = words(left);
        node.setAttribute("data-state", left <= 3600000 ? "soon" : "upcoming");
      } else if (node.getAttribute("data-state") !== "past") {
        node.textContent = left > -300000 ? "En cours" : node.textContent;
        node.setAttribute("data-state", left > -300000 ? "live" : "past");
      }
    }
  }
  tick();
  window.setInterval(function () { if (!document.hidden) { tick(); } }, 15000);

  /* -- 2. navigation sans rechargement ----------------------------------------------- */
  if (!window.fetch || !window.history || !window.DOMParser) { return; }
  var inflight = null;

  function sameCalendar(url) {
    return url.origin === window.location.origin && url.pathname === "/calendar";
  }

  function swap(doc) {
    var ok = true;
    REGIONS.forEach(function (id) {
      if (!doc.getElementById(id) || !document.getElementById(id)) { ok = false; }
    });
    if (!ok) { return false; }
    REGIONS.forEach(function (id) {
      var fresh = doc.getElementById(id);
      var old = document.getElementById(id);
      old.replaceWith(document.importNode(fresh, true));
    });
    var panel = document.getElementById("day-panel");
    if (panel) { panel.setAttribute("data-swap", "in"); }
    if (doc.title) { document.title = doc.title; }
    tick();
    return true;
  }

  function go(href, push, focusPanel) {
    var url = new URL(href, window.location.href);
    if (inflight) { inflight.abort(); }
    var controller = window.AbortController ? new AbortController() : null;
    inflight = controller;
    var panel = document.getElementById("day-panel");
    if (panel) { panel.setAttribute("aria-busy", "true"); }
    return fetch(url.href, {
      credentials: "same-origin",
      headers: { "Accept": "text/html" },
      signal: controller ? controller.signal : undefined
    }).then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      if (!swap(doc)) { throw new Error("shape"); }
      if (push) { window.history.pushState({ cal: true }, "", url.pathname + url.search); }
      if (focusPanel) {
        var title = document.getElementById("day-title");
        if (title) { title.focus({ preventScroll: true }); }
        var target = document.getElementById("day-panel");
        if (target && target.getBoundingClientRect().top > window.innerHeight * 0.8) {
          target.scrollIntoView({ block: "start" });
        }
      }
    }).catch(function (error) {
      if (error && error.name === "AbortError") { return; }
      window.location.assign(url.href);  // repli : navigation normale
    }).then(function () {
      if (inflight === controller) { inflight = null; }
      var again = document.getElementById("day-panel");
      if (again) { again.removeAttribute("aria-busy"); }
    });
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
    var link = event.target.closest ? event.target.closest("a[data-day-link]") : null;
    if (!link) { return; }
    var url = new URL(link.href, window.location.href);
    if (!sameCalendar(url)) { return; }
    event.preventDefault();
    go(url.href, true, true);
  });

  window.addEventListener("popstate", function () {
    if (sameCalendar(new URL(window.location.href))) { go(window.location.href, false, false); }
  });

  /* -- 3. flèches dans la grille ----------------------------------------------------- */
  document.addEventListener("keydown", function (event) {
    var keys = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    if (!(event.key in keys) || event.altKey || event.ctrlKey || event.metaKey) { return; }
    var current = document.activeElement;
    if (!current || !current.matches || !current.matches("#month-grid .cal-day")) { return; }
    var cells = Array.prototype.slice.call(document.querySelectorAll("#month-grid .cal-day"));
    var next = cells[cells.indexOf(current) + keys[event.key]];
    if (next) { event.preventDefault(); next.focus(); }
  });
})();
