/* Reports page : "Voir" opens the report in a large dialog, with the figures of its UTC
   window drawn as plain SVG and the stored text beside them, and a download button.
   Without JavaScript the link still works (/reports?report_id=N shows the text). */
(function () {
  "use strict";
  var dialog = document.getElementById("report-dialog");
  if (!dialog || typeof dialog.showModal !== "function") { return; }
  var NS = "http://www.w3.org/2000/svg";

  function svgEl(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    for (var key in attrs) { if (Object.prototype.hasOwnProperty.call(attrs, key)) { node.setAttribute(key, attrs[key]); } }
    if (parent) { parent.appendChild(node); }
    return node;
  }
  function el(tag, className, text, parent) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    if (parent) { parent.appendChild(node); }
    return node;
  }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function day(ts) { var d = new Date(ts * 1000); return pad(d.getUTCDate()) + "/" + pad(d.getUTCMonth() + 1); }
  function full(ts) { var d = new Date(ts * 1000); return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()) + " " + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()); }
  function money(v, signed) { return (v < 0 ? "-" : (signed && v > 0 ? "+" : "")) + Math.abs(v).toFixed(2) + " €"; }
  function tone(v) { return v > 0 ? "pos" : (v < 0 ? "neg" : ""); }

  function card(parent, title) {
    var box = el("section", "rp-card", null, parent);
    el("h3", null, title, box);
    return box;
  }

  /* Line + area for a series of [ts, value]. */
  function line(parent, points, label, fromZero) {
    var W = 560, H = 190, m = { l: 8, r: 8, t: 10, b: 20 };
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, "class": "rp-svg", role: "img", "aria-label": label }, parent);
    var xs = points.map(function (p) { return p[0]; }), ys = points.map(function (p) { return p[1]; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs) || x0 + 1;
    var pts = fromZero ? ys.concat([0]) : ys;
    var lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    if (hi === lo) { hi = lo + 1; }
    var base = fromZero ? Y0() : H - m.b;
    function Y0() { return m.t + (1 - (0 - lo) / (hi - lo)) * (H - m.t - m.b); }
    function X(v) { return m.l + (x1 === x0 ? 0.5 : (v - x0) / (x1 - x0)) * (W - m.l - m.r); }
    function Y(v) { return m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b); }
    if (fromZero) { svgEl("line", { x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), "class": "rp-grid" }, svg); }
    var d = points.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1); }).join(" ");
    var last = points[points.length - 1];
    svgEl("path", { d: d + " L" + X(last[0]).toFixed(1) + " " + base.toFixed(1) + " L" + X(points[0][0]).toFixed(1) + " " + base.toFixed(1) + " Z", "class": "rp-area " + (last[1] >= points[0][1] ? "up" : "down") }, svg);
    svgEl("path", { d: d, "class": "rp-line " + (last[1] >= points[0][1] ? "up" : "down") }, svg);
    svgEl("text", { x: m.l, y: H - 4, "class": "rp-axis" }, svg).textContent = day(x0);
    svgEl("text", { x: W - m.r, y: H - 4, "text-anchor": "end", "class": "rp-axis" }, svg).textContent = day(x1);
    svgEl("text", { x: W - m.r, y: m.t + 8, "text-anchor": "end", "class": "rp-axis" }, svg).textContent = money(last[1]);
  }

  /* Signed columns per UTC day, with a native title tooltip and a table alternative. */
  function columns(parent, days) {
    var W = 560, H = 170, m = { t: 10, b: 20 };
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, "class": "rp-svg", role: "img", "aria-label": "Résultat par jour UTC" }, parent);
    var vals = days.map(function (d) { return d.value; });
    var lo = Math.min.apply(null, vals.concat([0])), hi = Math.max.apply(null, vals.concat([0]));
    if (hi === lo) { hi = lo + 1; }
    function Y(v) { return m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b); }
    var slot = W / days.length, bw = Math.min(40, Math.max(3, slot - 3));
    svgEl("line", { x1: 0, x2: W, y1: Y(0), y2: Y(0), "class": "rp-grid" }, svg);
    days.forEach(function (d, i) {
      var r = svgEl("rect", { x: (i * slot + (slot - bw) / 2).toFixed(1), y: Math.min(Y(d.value), Y(0)).toFixed(1), width: bw.toFixed(1), height: Math.max(2, Math.abs(Y(d.value) - Y(0))).toFixed(1), rx: 2, "class": "rp-bar " + (d.value >= 0 ? "up" : "down") }, svg);
      svgEl("title", {}, r).textContent = day(d.ts) + " : " + money(d.value, true);
    });
    svgEl("text", { x: 0, y: H - 4, "class": "rp-axis" }, svg).textContent = day(days[0].ts);
    svgEl("text", { x: W, y: H - 4, "text-anchor": "end", "class": "rp-axis" }, svg).textContent = day(days[days.length - 1].ts);
  }

  function bars(parent, items) {
    var max = Math.max.apply(null, items.map(function (i) { return Math.abs(i.value); })) || 1;
    var list = el("div", "rp-bars", null, parent);
    items.forEach(function (it) {
      var row = el("div", "rp-bar-row", null, list);
      el("span", "rp-bar-name", it.name, row);
      var track = el("span", "rp-bar-track", null, row);
      el("i", it.value >= 0 ? "up" : "down", null, track).style.width = (Math.abs(it.value) / max * 100) + "%";
      el("span", "rp-bar-val " + tone(it.value), (it.value > 0 ? "▲ " : (it.value < 0 ? "▼ " : "")) + money(it.value, true), row);
    });
  }

  function render(data) {
    dialog.innerHTML = "";
    var head = el("header", "rp-head", null, dialog);
    var titles = el("div", null, null, head);
    el("h2", null, "Rapport " + data.period, titles).id = "report-title";
    el("p", "muted", full(data.window[0]) + " → " + full(data.window[1]) + " UTC", titles);
    var actions = el("div", "rp-actions", null, head);
    var dl = el("a", "rp-btn", "Télécharger", actions);
    dl.href = "/export/reports/" + data.id + ".txt";
    dl.setAttribute("download", "rapport-" + data.id + ".txt");
    var close = el("button", "rp-btn", "Fermer", actions);
    close.type = "button";
    close.addEventListener("click", function () { dialog.close(); });

    var body = el("div", "rp-body", null, dialog);
    var k = data.kpis;
    var stats = el("div", "rp-kpis", null, body);
    [["Résultat net", money(k.net, true), tone(k.net)], ["Trades", String(k.trades) + " (" + k.wins + " gagnants)", ""],
      ["Réussite", k.win_rate == null ? "n/d" : (k.win_rate * 100).toFixed(1) + " %", ""],
      ["Facteur de profit", k.profit_factor == null ? "n/d" : k.profit_factor.toFixed(2), ""],
      ["Meilleur / pire", k.best == null ? "n/d" : money(k.best, true) + " / " + money(k.worst, true), ""],
      ["Drawdown max", money(k.max_drawdown), ""]].forEach(function (s) {
      var c = el("div", "rp-kpi", null, stats);
      el("span", "rp-kpi-l", s[0], c);
      el("strong", "rp-kpi-v " + s[2], s[1], c);
    });

    var grid = el("div", "rp-grid-2", null, body);
    var any = false;
    if (data.equity.length > 1) { line(card(grid, "Équité du compte"), data.equity, "Équité du compte sur la période"); any = true; }
    if (data.cumulative.length > 1) { line(card(grid, "Résultat cumulé des trades"), data.cumulative, "Résultat cumulé des trades", true); any = true; }
    if (data.days.length) { columns(card(grid, "Résultat par jour (UTC)"), data.days); any = true; }
    if (data.markets.length) { bars(card(grid, "Résultat par marché"), data.markets); any = true; }
    if (!any) { el("p", "muted", "Aucun trade clôturé ni instantané de compte dans cette fenêtre : pas de graphique.", body); }

    var text = card(body, "Texte du rapport");
    el("pre", "rp-text", data.content, text);
    close.focus();
  }

  function open(id, trigger) {
    dialog.innerHTML = "";
    el("p", "muted", "Chargement du rapport…", dialog);
    dialog.showModal();
    fetch("/reports/" + id + "/detail", { headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
      .then(render)
      .catch(function () {
        dialog.innerHTML = "";
        el("p", "muted", "Impossible de charger ce rapport.", dialog);
        var b = el("button", "rp-btn", "Fermer", dialog);
        b.type = "button"; b.addEventListener("click", function () { dialog.close(); });
      });
    dialog.addEventListener("close", function () { if (trigger) { trigger.focus(); } }, { once: true });
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("a[data-report]") : null;
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.button) { return; }
    event.preventDefault();
    open(link.getAttribute("data-report"), link);
  });
  dialog.addEventListener("click", function (event) { if (event.target === dialog) { dialog.close(); } });
})();
