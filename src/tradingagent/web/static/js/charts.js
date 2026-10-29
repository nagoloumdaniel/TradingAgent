/* NexaGold dashboard : interactive charts, plain SVG, no library, no network.

   Progressive enhancement. The server embeds the numbers it already read (a JSON block,
   #ta-charts); this file only draws them. Nothing is computed that matters: periods and
   per-day buckets are sums of stored trade results, shown with their dates in UTC.

   Every chart has: a text summary (aria-label), a keyboard-operable cursor (a slider, arrow
   keys / Home / End), a touch cursor (pointer events), a table alternative, and colour that
   never carries the meaning alone (sign, arrow glyphs and numbers always say it too). */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var DATA = {};
  var holder = document.getElementById("ta-charts");
  if (holder) { try { DATA = JSON.parse(holder.textContent); } catch (error) { DATA = {}; } }
  var DAY = 86400;
  var uid = 0;

  /* -- helpers ---------------------------------------------------------------------- */
  function svgEl(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    for (var key in attrs) { if (Object.prototype.hasOwnProperty.call(attrs, key)) { node.setAttribute(key, attrs[key]); } }
    if (parent) { parent.appendChild(node); }
    return node;
  }
  function html(tag, className, text, parent) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    if (parent) { parent.appendChild(node); }
    return node;
  }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function utcDay(ts) {
    var d = new Date(ts * 1000);
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate());
  }
  function utcTime(ts) {
    var d = new Date(ts * 1000);
    return utcDay(ts) + " " + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + " UTC";
  }
  function money(value, signed) {
    var sign = value < 0 ? "-" : (signed && value > 0 ? "+" : "");
    return sign + Math.abs(value).toFixed(2) + " €";
  }
  function tone(value) { return value > 0 ? "pos" : (value < 0 ? "neg" : ""); }
  function arrow(value) { return value > 0 ? "▲" : (value < 0 ? "▼" : "▬"); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function nice(v) { return Math.abs(v) >= 1000 ? v.toFixed(0) : (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2)); }

  function empty(box, message, href, action) {
    box.innerHTML = "";
    box.removeAttribute("aria-busy");
    box.classList.remove("is-loading");
    var wrap = html("div", "chart-empty", null, box);
    html("p", "muted", message, wrap);
    if (href) { var link = html("a", "chart-empty-action", action, wrap); link.href = href; }
  }

  /* Cursor + tooltip shared by every chart. `locate(px)` maps an x in svg space to an index,
     `show(i)` paints the marker and returns {x, title, lines[]} for the tooltip. */
  function interact(host, svg, count, locate, show, hide, label) {
    var tip = html("div", "chart-tip", null, host);
    tip.setAttribute("aria-hidden", "true");
    var current = -1;
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("role", "slider");
    svg.setAttribute("aria-label", label);
    svg.setAttribute("aria-orientation", "horizontal");
    svg.setAttribute("aria-valuemin", "1");
    svg.setAttribute("aria-valuemax", String(count));

    function paint(i, fromKeyboard) {
      i = clamp(i, 0, count - 1);
      current = i;
      var info = show(i);
      tip.innerHTML = "";
      html("strong", null, info.title, tip);
      info.lines.forEach(function (line) {
        var row = html("span", line.tone || "", line.text, tip);
        row.className = "tip-line " + (line.tone || "");
      });
      tip.classList.add("on");
      var hostWidth = host.clientWidth;
      var width = tip.offsetWidth;
      var scale = hostWidth / (svg.viewBox.baseVal.width || hostWidth);
      var px = info.x * scale;
      var left = px + 14 + width > hostWidth ? px - width - 14 : px + 14;
      tip.style.transform = "translate(" + Math.max(0, left).toFixed(0) + "px, 6px)";
      svg.setAttribute("aria-valuenow", String(i + 1));
      svg.setAttribute("aria-valuetext", info.title + " : " + info.lines.map(function (l) { return l.text; }).join(", "));
      if (fromKeyboard) { svg.focus({ preventScroll: true }); }
    }
    function clear() { current = -1; tip.classList.remove("on"); hide(); }

    function pointerIndex(event) {
      var box = svg.getBoundingClientRect();
      var ratio = (svg.viewBox.baseVal.width || box.width) / box.width;
      return locate((event.clientX - box.left) * ratio);
    }
    svg.addEventListener("pointermove", function (event) { paint(pointerIndex(event), false); });
    svg.addEventListener("pointerdown", function (event) { paint(pointerIndex(event), false); });
    svg.addEventListener("pointerleave", function (event) { if (event.pointerType !== "touch") { clear(); } });
    svg.addEventListener("blur", clear);
    svg.addEventListener("keydown", function (event) {
      var step = 0;
      if (event.key === "ArrowRight" || event.key === "ArrowUp") { step = 1; }
      else if (event.key === "ArrowLeft" || event.key === "ArrowDown") { step = -1; }
      else if (event.key === "PageUp") { step = 10; }
      else if (event.key === "PageDown") { step = -10; }
      else if (event.key === "Home") { event.preventDefault(); paint(0, true); return; }
      else if (event.key === "End") { event.preventDefault(); paint(count - 1, true); return; }
      else if (event.key === "Escape") { clear(); return; }
      if (!step) { return; }
      event.preventDefault();
      paint((current < 0 ? count - 1 : current) + step, true);
    });
    return { paint: paint, clear: clear };
  }

  function observe(box, draw) {
    var last = 0;
    function run() {
      var width = Math.round(box.clientWidth);
      if (!width || width === last) { return; }
      last = width;
      draw(width);
    }
    if (window.ResizeObserver) {
      var queued = false;
      new ResizeObserver(function () {
        if (queued) { return; }
        queued = true;
        window.requestAnimationFrame(function () { queued = false; run(); });
      }).observe(box);
    } else { window.addEventListener("resize", run); }
    run();
    return function force() { last = 0; run(); };
  }

  function tableAlt(parent, caption, headers, rows) {
    var details = html("details", "chart-table", null, parent);
    html("summary", null, "Voir les données en tableau", details);
    var wrap = html("div", "table-wrap", null, details);
    wrap.style.maxHeight = "16rem";
    var table = html("table", null, null, wrap);
    html("caption", "sr-only", caption, table);
    var head = html("tr", null, null, html("thead", null, null, table));
    headers.forEach(function (h, index) {
      var th = html("th", index ? "num" : "", h, head);
      th.scope = "col";
    });
    var body = html("tbody", null, null, table);
    rows.forEach(function (row) {
      var tr = html("tr", null, null, body);
      row.forEach(function (cell, index) { html("td", index ? "num" : "", cell, tr); });
    });
  }

  /* -- equity curve ----------------------------------------------------------------- */
  var RANGES = [["1J", DAY, "1 jour"], ["1S", 7 * DAY, "1 semaine"], ["1M", 30 * DAY, "1 mois"], ["Tout", 0, "tout l'historique"]];

  function equityChart(box) {
    var all = DATA.equity || [];
    if (all.length < 2) {
      empty(box, "Pas encore assez d'instantanés de compte pour tracer la courbe d'équité.", "/system", "Voir l'état du système");
      return;
    }
    var endTs = all[all.length - 1][0];
    var chosen = RANGES[3];
    box.innerHTML = "";
    box.removeAttribute("aria-busy");
    box.classList.remove("is-loading");

    var bar = html("div", "chart-bar", null, box);
    var head = html("div", "chart-head", null, bar);
    var headline = html("div", "chart-headline", null, head);
    var big = html("span", "chart-big", null, headline);
    var delta = html("span", "chart-delta", null, headline);
    var group = html("div", "seg", null, bar);
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Période de la courbe");
    var buttons = RANGES.map(function (range) {
      var count = points(range).length;
      var b = html("button", "seg-btn", range[0], group);
      b.type = "button";
      b.title = range[2];
      b.setAttribute("aria-pressed", String(range === chosen));
      if (count < 2) { b.disabled = true; b.title = range[2] + " : pas assez de points"; }
      b.addEventListener("click", function () {
        chosen = range;
        buttons.forEach(function (x, i) { x.setAttribute("aria-pressed", String(RANGES[i] === chosen)); });
        force();
      });
      return b;
    });
    var host = html("div", "chart-host", null, box);
    var live = html("p", "sr-only", null, box);
    live.setAttribute("aria-live", "polite");
    var alt = html("div", "chart-alt", null, box);

    function points(range) {
      if (!range[1]) { return all; }
      var from = endTs - range[1];
      return all.filter(function (p) { return p[0] >= from; });
    }

    function draw(width) {
      var pts = points(chosen);
      if (pts.length < 2) { chosen = RANGES[3]; pts = all; }
      var height = width < 520 ? 190 : 250;
      var m = { l: 4, r: 4, t: 14, b: 22 };
      var lo = Infinity, hi = -Infinity;
      pts.forEach(function (p) { if (p[1] < lo) { lo = p[1]; } if (p[1] > hi) { hi = p[1]; } });
      var spread = hi - lo || Math.max(1, Math.abs(hi) * 0.01);
      lo -= spread * 0.08; hi += spread * 0.08;
      var t0 = pts[0][0], t1 = pts[pts.length - 1][0];
      function x(ts) { return m.l + (t1 === t0 ? 0 : (ts - t0) / (t1 - t0)) * (width - m.l - m.r); }
      function y(v) { return m.t + (1 - (v - lo) / (hi - lo)) * (height - m.t - m.b); }
      var first = pts[0][1], last = pts[pts.length - 1][1];
      var change = last - first;
      var trend = change > 0 ? "up" : (change < 0 ? "down" : "flat");

      host.innerHTML = "";
      var svg = svgEl("svg", { "class": "eq " + trend, viewBox: "0 0 " + width + " " + height, width: "100%", height: height }, host);
      var gid = "eqg" + (++uid);
      var defs = svgEl("defs", {}, svg);
      var grad = svgEl("linearGradient", { id: gid, x1: "0", y1: "0", x2: "0", y2: "1" }, defs);
      svgEl("stop", { offset: "0", "class": "g0" }, grad);
      svgEl("stop", { offset: "1", "class": "g1" }, grad);
      [0, 1, 2, 3].forEach(function (k) {
        var v = lo + (hi - lo) * (k / 3);
        var gy = y(v);
        svgEl("line", { "class": "grid", x1: 0, x2: width, y1: gy, y2: gy }, svg);
        var tx = svgEl("text", { "class": "ax", x: 4, y: gy - 4 }, svg);
        tx.textContent = nice(v);
      });
      [t0, t1].forEach(function (ts, k) {
        var tx = svgEl("text", { "class": "ax", x: k ? width - 4 : 4, y: height - 6, "text-anchor": k ? "end" : "start" }, svg);
        tx.textContent = (t1 - t0 <= 2 * DAY) ? utcTime(ts).slice(5, 16) : utcDay(ts);
      });
      var d = pts.map(function (p, i) { return (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1); }).join(" ");
      svgEl("path", { "class": "area", d: d + " L" + x(t1).toFixed(1) + "," + (height - m.b) + " L" + x(t0).toFixed(1) + "," + (height - m.b) + " Z", fill: "url(#" + gid + ")" }, svg);
      svgEl("path", { "class": "line", d: d }, svg);
      var cursor = svgEl("g", { "class": "cursor", visibility: "hidden" }, svg);
      var vline = svgEl("line", { "class": "vline", y1: m.t, y2: height - m.b }, cursor);
      var dot = svgEl("circle", { "class": "dot-m", r: 5 }, cursor);

      var words = trend === "up" ? "en hausse" : (trend === "down" ? "en baisse" : "stable");
      svg.setAttribute("data-summary", "");
      big.textContent = money(last);
      delta.className = "chart-delta " + tone(change);
      var pct = first ? (change / Math.abs(first)) * 100 : 0;
      delta.textContent = arrow(change) + " " + money(change, true) + " (" + (pct > 0 ? "+" : "") + pct.toFixed(2) + " %) sur " + chosen[2];
      var summary = "Courbe d'équité, " + chosen[2] + " : de " + money(first) + " à " + money(last) + ", " + words + ", entre " + money(Math.min.apply(null, pts.map(function (p) { return p[1]; }))) + " et " + money(Math.max.apply(null, pts.map(function (p) { return p[1]; }))) + ", du " + utcDay(t0) + " au " + utcDay(t1) + " (UTC).";
      host.setAttribute("data-summary", summary);

      var xs = pts.map(function (p) { return x(p[0]); });
      function locate(px) {
        var best = 0, gap = Infinity;
        for (var i = 0; i < xs.length; i++) {
          var g = Math.abs(xs[i] - px);
          if (g < gap) { gap = g; best = i; }
        }
        return best;
      }
      var ctl = interact(host, svg, pts.length, locate, function (i) {
        var p = pts[i];
        cursor.setAttribute("visibility", "visible");
        vline.setAttribute("x1", xs[i]); vline.setAttribute("x2", xs[i]);
        dot.setAttribute("cx", xs[i]); dot.setAttribute("cy", y(p[1]));
        var from = p[1] - first;
        return {
          x: xs[i],
          title: utcTime(p[0]),
          lines: [
            { text: "Équité " + money(p[1]) },
            { text: arrow(from) + " " + money(from, true) + " vs début", tone: tone(from) }
          ]
        };
      }, function () { cursor.setAttribute("visibility", "hidden"); }, summary + " Utilisez les flèches pour parcourir les points.");
      void ctl;
      live.textContent = summary;

      alt.innerHTML = "";
      var step = Math.max(1, Math.ceil(pts.length / 120));
      var rows = [];
      for (var i = 0; i < pts.length; i += step) { rows.push([utcTime(pts[i][0]), money(pts[i][1])]); }
      tableAlt(alt, "Courbe d'équité, " + chosen[2] + ", horodatage UTC", ["Horodatage (UTC)", "Équité"], rows);
    }
    var force = observe(host, draw);
  }

  /* -- result bars : per UTC day or per trade -------------------------------------- */
  function dayBuckets(trades, days, endTs) {
    var endDay = Math.floor(endTs / DAY);
    var map = {};
    trades.forEach(function (t) {
      var key = Math.floor(t[0] / DAY);
      var slot = map[key] || (map[key] = { pnl: 0, n: 0 });
      slot.pnl += t[1]; slot.n += 1;
    });
    var out = [];
    for (var k = endDay - days + 1; k <= endDay; k++) {
      var s = map[k];
      out.push({ ts: k * DAY, pnl: s ? s.pnl : 0, n: s ? s.n : 0 });
    }
    return out;
  }

  function barsChart(box) {
    var trades = DATA.trades || [];
    var mode = box.getAttribute("data-mode") === "trade" ? "trade" : "day";
    if (!trades.length) {
      empty(box, "Aucun trade clôturé pour le moment : les barres apparaissent après la première clôture.", "/trades", "Ouvrir les trades");
      return;
    }
    var endTs = DATA.now || trades[trades.length - 1][0];
    box.innerHTML = "";
    box.removeAttribute("aria-busy");
    box.classList.remove("is-loading");
    var bar = html("div", "chart-bar", null, box);
    var headline = html("div", "chart-headline", null, bar);
    var big = html("span", "chart-big", null, headline);
    var sub = html("span", "chart-delta", null, headline);
    var group = html("div", "seg", null, bar);
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Regroupement des barres");
    var modes = [["day", "Par jour"], ["trade", "Par trade"]].map(function (m) {
      var b = html("button", "seg-btn", m[1], group);
      b.type = "button";
      b.setAttribute("aria-pressed", String(m[0] === mode));
      b.addEventListener("click", function () {
        mode = m[0];
        modes.forEach(function (x, i) { x.setAttribute("aria-pressed", String((i === 0 ? "day" : "trade") === mode)); });
        force();
      });
      return b;
    });
    var host = html("div", "chart-host", null, box);
    var alt = html("div", "chart-alt", null, box);

    function draw(width) {
      var items;
      if (mode === "day") {
        items = dayBuckets(trades, width < 520 ? 14 : 30, endTs).map(function (b) {
          return { title: utcDay(b.ts), pnl: b.pnl, n: b.n, label: utcDay(b.ts).slice(5) };
        });
      } else {
        items = trades.slice(-(width < 520 ? 24 : 48)).map(function (t) {
          return { title: utcTime(t[0]), pnl: t[1], n: 1, label: t[2], symbol: t[2] };
        });
      }
      var height = width < 520 ? 170 : 210;
      var m = { t: 14, b: 22 };
      var lo = 0, hi = 0, total = 0, count = 0;
      items.forEach(function (it) { lo = Math.min(lo, it.pnl); hi = Math.max(hi, it.pnl); total += it.pnl; count += it.n; });
      var span = (hi - lo) || 1;
      function y(v) { return m.t + (1 - (v - lo) / span) * (height - m.t - m.b); }
      var y0 = y(0);
      var slot = width / items.length;
      var gap = slot > 6 ? 2 : 1;
      var bw = Math.max(2, slot - gap);

      host.innerHTML = "";
      var svg = svgEl("svg", { "class": "bars", viewBox: "0 0 " + width + " " + height, width: "100%", height: height }, host);
      svgEl("line", { "class": "grid", x1: 0, x2: width, y1: y0, y2: y0 }, svg);
      var tx = svgEl("text", { "class": "ax", x: 4, y: y0 - 4 }, svg);
      tx.textContent = "0";
      var rects = items.map(function (it, i) {
        var top = Math.min(y(it.pnl), y0), h = Math.abs(y(it.pnl) - y0);
        var r = svgEl("rect", {
          "class": "b " + (it.pnl > 0 ? "up" : (it.pnl < 0 ? "down" : "nil")),
          x: (i * slot + gap / 2).toFixed(1), y: top.toFixed(1), width: bw.toFixed(1), height: Math.max(it.pnl === 0 ? 1 : 2, h).toFixed(1), rx: 2
        }, svg);
        return r;
      });
      [0, items.length - 1].forEach(function (i, k) {
        var t = svgEl("text", { "class": "ax", x: k ? width - 4 : 4, y: height - 6, "text-anchor": k ? "end" : "start" }, svg);
        t.textContent = items[i].label;
      });
      big.textContent = money(total, true);
      big.className = "chart-big " + tone(total);
      sub.className = "chart-delta";
      sub.textContent = count + " trade(s) · " + (mode === "day" ? items.length + " derniers jours UTC" : items.length + " derniers trades");
      var summary = "Barres du résultat " + (mode === "day" ? "par jour UTC" : "par trade") + " : total " + money(total, true) + " sur " + count + " trade(s), de " + items[0].title + " à " + items[items.length - 1].title + ".";
      host.setAttribute("data-summary", summary);

      var prev = -1;
      interact(host, svg, items.length, function (px) { return clamp(Math.floor(px / slot), 0, items.length - 1); }, function (i) {
        if (prev >= 0) { rects[prev].classList.remove("hot"); }
        rects[i].classList.add("hot"); prev = i;
        var it = items[i];
        var lines = [{ text: arrow(it.pnl) + " " + money(it.pnl, true), tone: tone(it.pnl) }];
        if (mode === "day") { lines.push({ text: it.n ? it.n + " trade(s)" : "aucun trade" }); }
        else { lines.push({ text: it.symbol }); }
        return { x: i * slot + slot / 2, title: it.title, lines: lines };
      }, function () { if (prev >= 0) { rects[prev].classList.remove("hot"); prev = -1; } }, summary + " Utilisez les flèches pour parcourir les barres.");

      alt.innerHTML = "";
      tableAlt(alt, "Résultat " + (mode === "day" ? "par jour" : "par trade") + " (UTC)", ["Date (UTC)", "Résultat", mode === "day" ? "Trades" : "Marché"],
        items.slice().reverse().map(function (it) { return [it.title, money(it.pnl, true), mode === "day" ? String(it.n) : it.symbol]; }));
    }
    var force = observe(host, draw);
  }

  /* -- market split : donut + legend ----------------------------------------------- */
  function donutChart(box) {
    var markets = (DATA.markets || []).filter(function (m) { return m.trades > 0; });
    if (!markets.length) {
      empty(box, "Aucun trade clôturé : la répartition par marché apparaît après la première clôture.", "/trades", "Ouvrir les trades");
      return;
    }
    box.innerHTML = "";
    box.removeAttribute("aria-busy");
    box.classList.remove("is-loading");
    var total = markets.reduce(function (s, m) { return s + m.trades; }, 0);
    var shades = [96, 72, 54, 42, 34, 30];
    var wrap = html("div", "donut-wrap", null, box);
    var host = html("div", "donut-host", null, wrap);
    var size = 148, r = 56, circ = 2 * Math.PI * r;
    var svg = svgEl("svg", { "class": "donut", viewBox: "0 0 " + size + " " + size, role: "img" }, host);
    var summary = "Répartition des " + total + " trades par marché : " + markets.map(function (m) { return m.name + " " + m.trades + " (" + Math.round(m.trades / total * 100) + " %), résultat " + money(m.net, true); }).join(" ; ") + ".";
    svg.setAttribute("aria-label", summary);
    svgEl("circle", { "class": "ring-bg", cx: size / 2, cy: size / 2, r: r }, svg);
    var centerA = svgEl("text", { "class": "c-main", x: size / 2, y: size / 2 + 2, "text-anchor": "middle" }, svg);
    var centerB = svgEl("text", { "class": "c-sub", x: size / 2, y: size / 2 + 18, "text-anchor": "middle" }, svg);
    var list = html("ul", "legend", null, wrap);
    var arcs = [], rows = [];
    var acc = 0;
    function idle() { centerA.textContent = String(total); centerB.textContent = "trades"; arcs.forEach(function (a) { a.classList.remove("hot", "dim"); }); rows.forEach(function (x) { x.classList.remove("hot"); }); }
    function focusOn(i) {
      arcs.forEach(function (a, k) { a.classList.toggle("hot", k === i); a.classList.toggle("dim", k !== i); });
      rows.forEach(function (x, k) { x.classList.toggle("hot", k === i); });
      centerA.textContent = Math.round(markets[i].trades / total * 100) + " %";
      centerB.textContent = markets[i].name;
    }
    markets.forEach(function (m, i) {
      var len = m.trades / total * circ;
      var gap = markets.length > 1 ? 2.5 : 0;
      var arc = svgEl("circle", {
        "class": "arc", cx: size / 2, cy: size / 2, r: r,
        "stroke-dasharray": Math.max(0.5, len - gap).toFixed(2) + " " + (circ - Math.max(0.5, len - gap)).toFixed(2),
        "stroke-dashoffset": (-acc).toFixed(2), transform: "rotate(-90 " + size / 2 + " " + size / 2 + ")"
      }, svg);
      arc.style.setProperty("--shade", shades[Math.min(i, shades.length - 1)] + "%");
      acc += len;
      arcs.push(arc);
      var li = html("li", "legend-row", null, list);
      li.setAttribute("tabindex", "0");
      var sw = html("span", "swatch", null, li);
      sw.style.setProperty("--shade", shades[Math.min(i, shades.length - 1)] + "%");
      html("span", "legend-name", m.name, li);
      html("span", "legend-n num", m.trades + " · " + Math.round(m.trades / total * 100) + " %", li);
      html("span", "legend-net num " + tone(m.net), arrow(m.net) + " " + money(m.net, true), li);
      rows.push(li);
      [arc, li].forEach(function (node) {
        node.addEventListener("pointerenter", function () { focusOn(i); });
        node.addEventListener("pointerleave", idle);
      });
      li.addEventListener("focus", function () { focusOn(i); });
      li.addEventListener("blur", idle);
    });
    idle();
  }

  /* -- sparklines inside KPI cards -------------------------------------------------- */
  function sparks() {
    Array.prototype.forEach.call(document.querySelectorAll("svg[data-spark]"), function (svg) {
      var kind = svg.getAttribute("data-spark");
      var w = 160, h = 34;
      svg.setAttribute("viewBox", "0 0 " + w + " " + h);
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("role", "img");
      if (kind === "equity") {
        var pts = (DATA.equity || []).slice(-60);
        if (pts.length < 2) { svg.remove(); return; }
        var lo = Math.min.apply(null, pts.map(function (p) { return p[1]; }));
        var hi = Math.max.apply(null, pts.map(function (p) { return p[1]; }));
        var sp = hi - lo || 1;
        var d = pts.map(function (p, i) { return (i ? "L" : "M") + (i / (pts.length - 1) * w).toFixed(1) + "," + (h - 3 - (p[1] - lo) / sp * (h - 6)).toFixed(1); }).join(" ");
        var delta = pts[pts.length - 1][1] - pts[0][1];
        svg.setAttribute("class", "mini " + (delta > 0 ? "up" : (delta < 0 ? "down" : "flat")));
        svgEl("path", { "class": "line", d: d }, svg);
        svg.setAttribute("aria-label", "Tendance de l'équité sur les " + pts.length + " derniers instantanés : " + arrow(delta) + " " + money(delta, true));
      } else if (kind === "pnl") {
        var days = dayBuckets(DATA.trades || [], 14, DATA.now || Math.floor(Date.now() / 1000));
        var mx = Math.max.apply(null, days.map(function (b) { return Math.abs(b.pnl); })) || 1;
        svg.setAttribute("class", "mini bars-mini");
        var mid = h / 2, bw = w / days.length;
        days.forEach(function (b, i) {
          var bh = Math.max(b.pnl === 0 ? 0 : 2, Math.abs(b.pnl) / mx * (mid - 2));
          svgEl("rect", { "class": b.pnl > 0 ? "up" : (b.pnl < 0 ? "down" : "nil"), x: (i * bw + 1).toFixed(1), y: (b.pnl >= 0 ? mid - bh : mid).toFixed(1), width: (bw - 2).toFixed(1), height: bh.toFixed(1) }, svg);
        });
        svgEl("line", { "class": "zero", x1: 0, x2: w, y1: mid, y2: mid }, svg);
        var sum = days.reduce(function (s, b) { return s + b.pnl; }, 0);
        svg.setAttribute("aria-label", "Résultat des 14 derniers jours UTC : " + money(sum, true));
      }
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-chart]"), function (box) {
    var kind = box.getAttribute("data-chart");
    if (kind === "equity") { equityChart(box); }
    else if (kind === "pnl-bars") { barsChart(box); }
    else if (kind === "markets") { donutChart(box); }
  });
  sparks();
})();
