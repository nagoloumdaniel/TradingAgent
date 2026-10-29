/* NexaGold dashboard : light interaction layer, progressive enhancement.
   Table sort and filter (current page only, the server stays the source of truth), one-click
   copy of identifiers, a collapsible navigation rail, and discreet keyboard shortcuts
   (g then a letter to go to a page, / to search, ? for help; can be switched off). */
(function () {
  "use strict";

  var KEYS_OFF = "ta-keys-off";
  function stored(key) { try { return localStorage.getItem(key); } catch (error) { return null; } }
  function store(key, value) {
    try { if (value == null) { localStorage.removeItem(key); } else { localStorage.setItem(key, value); } } catch (error) { /* private mode */ }
  }
  function el(tag, className, text, parent) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    if (parent) { parent.appendChild(node); }
    return node;
  }

  /* -- status line: one polite region for "copied" and similar ------------------------ */
  var status = el("div", "toast", null, document.body);
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  var statusTimer = 0;
  function say(message) {
    status.textContent = message;
    status.classList.add("on");
    window.clearTimeout(statusTimer);
    statusTimer = window.setTimeout(function () { status.classList.remove("on"); }, 1600);
  }

  /* -- tables : sort, filter, copy ---------------------------------------------------- */
  var COPY_HEADER = /ticket|\bid\b|identifiant|référence|reference|hash/i;
  var COPY_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/>' +
    '<path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>';

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) { return navigator.clipboard.writeText(text); }
    return new Promise(function (resolve, reject) {
      var area = el("textarea", "sr-only", text, document.body);
      area.select();
      try { document.execCommand("copy") ? resolve() : reject(new Error("copy")); } catch (error) { reject(error); }
      area.remove();
    });
  }

  function cellValue(row, index) {
    var cell = row.cells[index];
    if (!cell) { return ""; }
    return (cell.getAttribute("data-sort") || cell.textContent).trim();
  }
  var NUMBER = /^[-+−]?\d+(\.\d+)?\s*(€|%|s|ms)?$/;
  function compare(a, b) {
    if (NUMBER.test(a) && NUMBER.test(b)) {
      return parseFloat(a.replace("−", "-")) - parseFloat(b.replace("−", "-"));
    }
    return a.localeCompare(b, "fr", { numeric: true, sensitivity: "base" });
  }

  function enhance(wrap) {
    var table = wrap.querySelector("table");
    if (!table || !table.tHead || !table.tBodies.length || wrap.hasAttribute("data-plain")) { return; }
    var body = table.tBodies[0];
    var headRow = table.tHead.rows[table.tHead.rows.length - 1];
    var real = function () {
      return Array.prototype.filter.call(body.rows, function (row) {
        return !row.classList.contains("total") && !row.querySelector("td.empty");
      });
    };
    if (real().length < 2 || headRow.cells.length < 3) { return; }

    /* sort: the header becomes a button, aria-sort says the direction */
    var sortedBy = -1, direction = 1;
    Array.prototype.forEach.call(headRow.cells, function (th, index) {
      var label = th.textContent.trim();
      if (!label) { return; }
      th.textContent = "";
      var button = el("button", "th-sort", label, th);
      button.type = "button";
      button.setAttribute("title", "Trier la page courante par " + label);
      button.addEventListener("click", function () {
        direction = sortedBy === index ? -direction : 1;
        sortedBy = index;
        var rows = real();
        rows.sort(function (r1, r2) { return direction * compare(cellValue(r1, index), cellValue(r2, index)); });
        var anchor = body.querySelector("tr.total");
        rows.forEach(function (row) { body.insertBefore(row, anchor); });
        Array.prototype.forEach.call(headRow.cells, function (cell, k) {
          if (k === index) { cell.setAttribute("aria-sort", direction > 0 ? "ascending" : "descending"); }
          else { cell.removeAttribute("aria-sort"); }
        });
        say("Tri par " + label + (direction > 0 ? ", croissant" : ", décroissant"));
      });
    });

    /* filter: only worth a field when there is something to search through */
    if (real().length >= 8) {
      var tools = el("div", "tbl-tools");
      wrap.parentNode.insertBefore(tools, wrap);
      var input = el("input", null, null, tools);
      input.type = "search";
      input.placeholder = "Filtrer les lignes affichées";
      input.setAttribute("aria-label", "Filtrer les lignes affichées du tableau");
      input.autocomplete = "off";
      var count = el("span", "tbl-count", null, tools);
      count.setAttribute("aria-live", "polite");
      input.addEventListener("input", function () {
        var needle = input.value.trim().toLowerCase();
        var shown = 0, rows = real();
        rows.forEach(function (row) {
          var hit = !needle || row.textContent.toLowerCase().indexOf(needle) !== -1;
          row.hidden = !hit;
          if (hit) { shown += 1; }
        });
        count.textContent = needle ? shown + " / " + rows.length + " ligne(s)" : "";
      });
    }

    /* copy: identifiers get a one-click button */
    Array.prototype.forEach.call(headRow.cells, function (th, index) {
      if (!COPY_HEADER.test(th.textContent)) { return; }
      Array.prototype.forEach.call(body.rows, function (row) {
        var cell = row.cells[index];
        if (!cell || cell.classList.contains("empty") || cell.querySelector(".copy")) { return; }
        var text = cell.textContent.trim();
        if (!text || text === "-" || text === "n/a") { return; }
        var button = el("button", "copy", null, cell);
        button.type = "button";
        button.innerHTML = COPY_ICON;
        button.setAttribute("aria-label", "Copier " + text);
        button.setAttribute("title", "Copier");
        button.addEventListener("click", function () {
          copyText(text).then(function () {
            button.classList.add("done");
            say("Copié : " + text);
            window.setTimeout(function () { button.classList.remove("done"); }, 1200);
          }, function () { say("Copie impossible"); });
        });
      });
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll(".table-wrap"), enhance);

  /* -- navigation rail : compact on demand (desktop) --------------------------------- */
  (function rail() {
    var nav = document.getElementById("side-nav");
    if (!nav) { return; }
    var root = document.documentElement;
    if (stored("ta-rail") === "min") { root.dataset.rail = "min"; }
    var button = el("button", "rail-toggle", null, nav);
    button.type = "button";
    button.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';
    var text = el("span", null, "Réduire", button);
    function paint() {
      var min = root.dataset.rail === "min";
      button.setAttribute("aria-pressed", String(min));
      button.setAttribute("aria-label", min ? "Développer la navigation" : "Réduire la navigation");
      button.setAttribute("title", min ? "Développer la navigation" : "Réduire la navigation");
      text.textContent = min ? "Développer" : "Réduire";
    }
    button.addEventListener("click", function () {
      if (root.dataset.rail === "min") { delete root.dataset.rail; store("ta-rail", null); }
      else { root.dataset.rail = "min"; store("ta-rail", "min"); }
      paint();
    });
    paint();
  })();

  /* -- keyboard shortcuts ------------------------------------------------------------- */
  var GO = {
    o: ["/", "Vue d'ensemble"], p: ["/positions", "Positions"], t: ["/trades", "Trades"],
    r: ["/risk", "Risque"], s: ["/strategies", "Stratégies"], c: ["/scalping", "Scalping"],
    a: ["/ai-lab", "Laboratoire IA"], y: ["/system", "Système"], d: ["/reports", "Rapports"]
  };
  var dialog = null;
  function typing(target) {
    if (!target || !target.tagName) { return false; }
    var tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
  }
  function help() {
    if (!dialog) {
      dialog = el("dialog", "keys", null, document.body);
      dialog.setAttribute("aria-labelledby", "keys-title");
      var title = el("h2", null, "Raccourcis clavier", dialog);
      title.id = "keys-title";
      var list = el("dl", "keys-list", null, dialog);
      Object.keys(GO).forEach(function (k) {
        var row = el("div", null, null, list);
        el("dt", null, "g puis " + k, row);
        el("dd", null, GO[k][1], row);
      });
      [["/", "Aller à la recherche"], ["?", "Afficher cette aide"], ["Flèches", "Déplacer le curseur d'un graphique"]].forEach(function (pair) {
        var row = el("div", null, null, list);
        el("dt", null, pair[0], row);
        el("dd", null, pair[1], row);
      });
      var label = el("label", "keys-off", null, dialog);
      var box = el("input", null, null, label);
      box.type = "checkbox";
      box.checked = stored(KEYS_OFF) === "1";
      el("span", null, "Désactiver les raccourcis", label);
      box.addEventListener("change", function () { store(KEYS_OFF, box.checked ? "1" : null); say(box.checked ? "Raccourcis désactivés" : "Raccourcis activés"); });
      var close = el("button", null, "Fermer", dialog);
      close.type = "button";
      close.addEventListener("click", function () { dialog.close(); });
    }
    if (typeof dialog.showModal === "function" && !dialog.open) { dialog.showModal(); }
  }
  var pendingGo = 0;
  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) { return; }
    if (event.key === "?") { event.preventDefault(); help(); return; }
    if (stored(KEYS_OFF) === "1") { return; }
    if (pendingGo) {
      var target = GO[event.key.toLowerCase()];
      window.clearTimeout(pendingGo); pendingGo = 0;
      if (target) { event.preventDefault(); window.location.assign(target[0]); }
      return;
    }
    if (event.key === "g") { pendingGo = window.setTimeout(function () { pendingGo = 0; }, 1200); return; }
    if (event.key === "/") {
      var field = document.querySelector('input[type="search"]');
      if (field) { event.preventDefault(); field.focus(); }
    }
  });

  var foot = document.querySelector("footer");
  if (foot) {
    var hint = el("button", "keys-hint", "Raccourcis ?", foot);
    hint.type = "button";
    hint.setAttribute("aria-label", "Afficher les raccourcis clavier");
    hint.addEventListener("click", help);
  }
})();
