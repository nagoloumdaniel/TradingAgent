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
  /* Green / orange / red for charts that carry no sign of their own. `ratio` is the share of
     the largest value (0..1); by default more is worse, `goodHigh` flips it. The number is
     always printed next to the colour, so the colour never carries the meaning alone. */
  function tier(ratio, goodHigh) {
    var r = goodHigh ? 1 - ratio : ratio;
    return r >= 0.8 ? "bad" : (r >= 0.5 ? "warn" : "ok");
  }
  /* Donut identity is a single ink ramp (--shade) read through the legend, never a status
     hue: green / amber / red stay reserved for good / warning / bad. */
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
    var shades = [96, 76, 62, 54, 49, 46];
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

  /* ===================================================================================
     Charts of the other pages. Same rules: the numbers come from the server (#ta-charts),
     this file draws them; each chart has a text summary, a focusable cursor or rows, an
     empty state with one action, and never relies on colour alone.
     =================================================================================== */
  function ready(box) {
    box.innerHTML = "";
    box.removeAttribute("aria-busy");
    box.classList.remove("is-loading");
  }
  function fmtValue(kind, v, signed) {
    if (v == null || !isFinite(v)) { return "n/a"; }
    if (kind === "money") { return money(v, signed); }
    if (kind === "sec") {
      if (v < 90) { return Math.round(v) + " s"; }
      if (v < 5400) { return Math.round(v / 60) + " min"; }
      return (v / 3600).toFixed(1) + " h";
    }
    if (kind === "ms") { return Math.round(v) + " ms"; }
    if (kind === "pct") { return (v * 100).toFixed(0) + " %"; }
    if (kind === "int") { return String(Math.round(v)); }
    return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2);
  }

  /* Horizontal bars as focusable rows. items: {name, value, alt?, sub?, label?, cls?}.
     `alt` is a second, fainter bar drawn behind (worst case behind the median). */
  function renderBars(host, items, kind, opts) {
    opts = opts || {};
    host.innerHTML = "";
    var signed = !!opts.signed;
    var lo = 0, hi = 0;
    items.forEach(function (it) {
      lo = Math.min(lo, it.value, it.alt == null ? 0 : it.alt);
      hi = Math.max(hi, it.value, it.alt == null ? 0 : it.alt);
    });
    var span = (hi - lo) || 1;
    var zero = (0 - lo) / span * 100;
    var peak = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
    var list = html("div", "hb", null, host);
    list.setAttribute("role", "list");
    var note = html("p", "hb-note", opts.hint || "Survolez ou parcourez une ligne pour le détail.", host);
    note.setAttribute("aria-live", "polite");
    var rows = items.map(function (it) {
      var row = html("div", "hb-row", null, list);
      row.setAttribute("role", "listitem");
      row.tabIndex = 0;
      var valueText = it.label || fmtValue(kind, it.value, signed);
      var full = it.name + " : " + valueText + (it.sub ? " (" + it.sub + ")" : "");
      row.setAttribute("aria-label", full);
      html("span", "hb-name", it.name, row).title = it.name;
      var track = html("span", "hb-track", null, row);
      if (it.alt != null) {
        var back = html("span", "hb-fill alt", null, track);
        var a0 = Math.min(zero, (it.alt - lo) / span * 100), a1 = Math.max(zero, (it.alt - lo) / span * 100);
        back.style.left = a0 + "%"; back.style.width = Math.max(1.5, a1 - a0) + "%"; back.style.opacity = "0.35";
      }
      var fill = html("span", "hb-fill" + (it.cls ? " " + it.cls : (signed ? (it.value > 0 ? " up" : (it.value < 0 ? " down" : "")) : " " + tier(items.length > 1 ? Math.abs(it.value) / peak : 0, opts.goodHigh))), null, track);
      var p0 = Math.min(zero, (it.value - lo) / span * 100), p1 = Math.max(zero, (it.value - lo) / span * 100);
      fill.style.left = p0 + "%"; fill.style.width = Math.max(it.value === 0 ? 0 : 1.5, p1 - p0) + "%";
      if (lo < 0) { html("span", "hb-zero", null, track).style.left = zero + "%"; }
      var val = html("span", "hb-val " + (signed ? tone(it.value) : ""), (signed ? arrow(it.value) + " " : "") + valueText, row);
      void val;
      function on() {
        rows.forEach(function (r) { r.classList.remove("hot"); });
        row.classList.add("hot");
        note.textContent = full;
      }
      function off() { row.classList.remove("hot"); note.textContent = opts.hint || "Survolez ou parcourez une ligne pour le détail."; }
      row.addEventListener("pointerenter", on);
      row.addEventListener("pointerleave", off);
      row.addEventListener("focus", on);
      row.addEventListener("blur", off);
      return row;
    });
  }

  function hbarsChart(box) {
    var items = (DATA[box.getAttribute("data-key")] || []).map(function (it) {
      return { name: it.name, value: it.value, sub: it.sub };
    });
    if (!items.length) {
      empty(box, box.getAttribute("data-empty") || "Rien à tracer pour le moment.", "/trades", "Voir les trades");
      return;
    }
    ready(box);
    renderBars(box, items, box.getAttribute("data-fmt") || "money", { signed: false });
  }

  /* Exposure: buy and sell notional stacked per market. */
  function exposureChart(box) {
    var rows = (DATA.exposure || []).filter(function (r) { return r.long || r.short; });
    if (!rows.length) {
      empty(box, "Aucune position ouverte : exposition nulle.", "/trades", "Voir les derniers trades");
      return;
    }
    ready(box);
    var max = Math.max.apply(null, rows.map(function (r) { return r.long + r.short; })) || 1;
    var list = html("div", "hb", null, box);
    list.setAttribute("role", "list");
    rows.forEach(function (r) {
      var row = html("div", "hb-row", null, list);
      row.setAttribute("role", "listitem"); row.tabIndex = 0;
      var text = r.name + " : achat " + money(r.long) + ", vente " + money(r.short);
      row.setAttribute("aria-label", text); row.title = text;
      html("span", "hb-name", r.name, row);
      var track = html("span", "hb-track", null, row);
      var a = html("span", "hb-fill up", null, track);
      a.style.left = "0"; a.style.width = (r.long / max * 100) + "%";
      var b = html("span", "hb-fill down", null, track);
      b.style.left = (r.long / max * 100) + "%"; b.style.width = (r.short / max * 100) + "%";
      html("span", "hb-val", money(r.long + r.short), row);
    });
    html("p", "hb-note", "Vert : achat. Rouge : vente. Notionnel = volume × prix d'entrée.", box);
  }

  function strategiesChart(box) {
    var rows = DATA.strategies || [];
    if (!rows.length) {
      empty(box, "Aucune stratégie n'a encore de trade clôturé : la comparaison apparaît après la première clôture.", "/trades", "Ouvrir les trades");
      return;
    }
    ready(box);
    var metrics = [["net", "Résultat", "money", true], ["pf", "Facteur de profit", "num", false], ["wr", "Réussite", "pct", false]];
    var current = metrics[0];
    var bar = html("div", "chart-bar", null, box);
    html("span", "chart-delta", rows.length + " stratégie(s) avec trades", bar);
    var group = html("div", "seg", null, bar);
    group.setAttribute("role", "group"); group.setAttribute("aria-label", "Mesure comparée");
    var buttons = metrics.map(function (m) {
      var b = html("button", "seg-btn", m[1], group);
      b.type = "button"; b.setAttribute("aria-pressed", String(m === current));
      b.addEventListener("click", function () { current = m; buttons.forEach(function (x, i) { x.setAttribute("aria-pressed", String(metrics[i] === current)); }); draw(); });
      return b;
    });
    var host = html("div", "chart-host", null, box);
    function draw() {
      var items = rows.filter(function (r) { return r[current[0]] != null; }).map(function (r) {
        return { name: r.name, value: r[current[0]], sub: r.trades + " trade(s)" };
      }).sort(function (a, b) { return b.value - a.value; });
      renderBars(host, items, current[2], { signed: current[3], goodHigh: !current[3] });
    }
    draw();
  }

  /* Risk gauges: used against limit. Each kind of limit gets its own shape (arc, ring, bullet,
     pips, steps); the state is also said in words and by an icon, never by colour alone. */
  function gaugeVisual(g, shape, ratio, el) {
    var pct = Math.min(100, ratio * 100);
    var box = html("div", "gauge-viz " + shape, null, el);
    var svg;
    if (shape === "arc" || shape === "ring") {
      var arc = shape === "arc";
      svg = svgEl("svg", { viewBox: arc ? "0 0 100 58" : "0 0 100 100", "class": "gv", "aria-hidden": "true" }, box);
      var d = arc ? "M 10 50 A 40 40 0 0 1 90 50" : "M 50 10 A 40 40 0 1 1 49.99 10";
      svgEl("path", { d: d, "class": "gv-track", pathLength: 100 }, svg);
      if (pct > 0) { svgEl("path", { d: d, "class": "gv-fill", pathLength: 100, "stroke-dasharray": pct.toFixed(1) + " 100" }, svg); }
      [0.5, 0.8].forEach(function (t) {
        var ang = arc ? Math.PI * (1 - t) : Math.PI * 2 * t - Math.PI / 2;
        var dy = arc ? -Math.sin(ang) : Math.sin(ang);
        svgEl("line", {
          x1: (50 + Math.cos(ang) * 35).toFixed(1), y1: (50 + dy * 35).toFixed(1),
          x2: (50 + Math.cos(ang) * 45).toFixed(1), y2: (50 + dy * 45).toFixed(1), "class": "gv-tick"
        }, svg);
      });
      svgEl("text", { x: 50, y: arc ? 49 : 56, "text-anchor": "middle", "class": "gv-num" }, svg).textContent = Math.round(ratio * 100) + " %";
    } else if (shape === "bullet") {
      svg = svgEl("svg", { viewBox: "0 0 200 30", "class": "gv", preserveAspectRatio: "none", "aria-hidden": "true" }, box);
      svgEl("rect", { x: 0, y: 8, width: 100, height: 14, rx: 3, "class": "gv-zone ok" }, svg);
      svgEl("rect", { x: 100, y: 8, width: 60, height: 14, "class": "gv-zone warn" }, svg);
      svgEl("rect", { x: 160, y: 8, width: 40, height: 14, rx: 3, "class": "gv-zone bad" }, svg);
      svgEl("rect", { x: 0, y: 12, width: (pct * 2).toFixed(1), height: 6, rx: 3, "class": "gv-fill" }, svg);
      var mx = clamp(pct * 2, 2, 198);
      svgEl("path", { d: "M " + mx + " 4 L " + mx + " 26", "class": "gv-mark" }, svg);
    } else {
      var limit = Math.max(1, Math.round(g.limit));
      var n = Math.min(limit, 20);
      var filled = limit > 20 ? Math.round(Math.min(1, ratio) * n) : Math.min(n, Math.round(g.used || 0));
      if (g.used > 0 && filled === 0) { filled = 1; }
      if (shape === "pips") {
        var row = html("div", "pips", null, box);
        for (var i = 0; i < n; i++) { html("i", i < filled ? "on" : "", null, row); }
      } else {
        svg = svgEl("svg", { viewBox: "0 0 " + (n * 12) + " 34", "class": "gv", preserveAspectRatio: "none", "aria-hidden": "true" }, box);
        for (var k = 0; k < n; k++) {
          var h = 8 + (k + 1) / n * 24;
          svgEl("rect", { x: k * 12 + 1, y: 34 - h, width: 9, height: h, rx: 2, "class": "gv-step" + (k < filled ? " on" : "") }, svg);
        }
      }
    }
    return box;
  }

  /* A limit that does not exist is not drawn as a gauge: there is no ratio to be 80 % of.
     The tile counts (big number, per-market breakdown) and says "illimité" in words. */
  function unlimitedGauge(g, wrap) {
    var el = html("div", "gauge unlimited", null, wrap);
    el.setAttribute("role", "listitem");
    var top = html("div", "gauge-top", null, el);
    html("strong", null, g.name, top);
    html("span", null, "illimité", top);
    var viz = html("div", "gauge-viz count", null, el);
    html("span", "gv-count", String(Math.round(g.used || 0)), viz);
    (g.parts || []).forEach(function (part) {
      var chip = html("span", "gv-part", null, viz);
      html("span", null, part.name, chip);
      html("b", null, String(part.value), chip);
    });
    var st = html("div", "gauge-state", null, el);
    var svg = svgEl("svg", { "class": "icon", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, st);
    svgEl("use", { href: "#i-info" }, svg);
    html("span", null, "Aucune limite : comptage seul", st);
  }

  function gaugesChart(box) {
    var items = (DATA.gauges || []).filter(function (g) { return g.limit || g.unlimited; });
    if (!items.length) {
      empty(box, "Configuration de risque illisible ou absente : aucune limite à comparer.", "/system", "Voir le système");
      return;
    }
    ready(box);
    var wrap = html("div", "gauges", null, box);
    wrap.setAttribute("role", "list");
    var seen = { money: 0, pct: 0, int: 0 };
    var kinds = { money: ["arc", "ring"], pct: ["bullet"], int: ["pips", "steps"] };
    items.forEach(function (g) {
      if (g.unlimited) { unlimitedGauge(g, wrap); return; }
      var ratio = g.ratio == null ? 0 : g.ratio;
      var state = ratio >= 0.8 ? "bad" : (ratio >= 0.5 ? "warn" : "ok");
      var el = html("div", "gauge " + state, null, wrap);
      el.setAttribute("role", "listitem");
      var list = kinds[g.kind] || kinds.int;
      var shape = list[(seen[g.kind] || 0) % list.length];
      seen[g.kind] = (seen[g.kind] || 0) + 1;
      var top = html("div", "gauge-top", null, el);
      html("strong", null, g.name, top);
      html("span", null, g.text, top);
      var viz = gaugeVisual(g, shape, ratio, el);
      viz.setAttribute("role", "meter");
      viz.setAttribute("aria-label", g.name + " : " + g.text);
      viz.setAttribute("aria-valuemin", "0"); viz.setAttribute("aria-valuemax", "100");
      viz.setAttribute("aria-valuenow", String(Math.min(100, Math.round(ratio * 100))));
      var st = html("div", "gauge-state", null, el);
      var glyph = state === "bad" ? "x-circle" : (state === "warn" ? "alert-triangle" : "check-circle");
      var svg = svgEl("svg", { "class": "icon", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, st);
      svgEl("use", { href: "#i-" + glyph }, svg);
      html("span", null, Math.round(ratio * 100) + " % de la limite" + (state === "bad" ? " · élevé" : (state === "warn" ? " · à surveiller" : "")), st);
    });
  }

  /* Signed columns with the shared cursor: items {title, value, label?, lines?[]}. */
  function renderCols(host, items, kind, summaryLabel) {
    host.innerHTML = "";
    var width = Math.round(host.clientWidth) || 300;
    var height = width < 520 ? 150 : 190;
    var m = { t: 12, b: 22 };
    var lo = 0, hi = 0, total = 0;
    items.forEach(function (it) { lo = Math.min(lo, it.value); hi = Math.max(hi, it.value); total += it.value; });
    var span = (hi - lo) || 1;
    function y(v) { return m.t + (1 - (v - lo) / span) * (height - m.t - m.b); }
    var y0 = y(0), slot = width / items.length, gap = slot > 6 ? 2 : 1, bw = Math.min(44, Math.max(2, slot - gap)), pad0 = (slot - bw) / 2;
    var svg = svgEl("svg", { "class": "bars", viewBox: "0 0 " + width + " " + height, width: "100%", height: height }, host);
    svgEl("line", { "class": "grid", x1: 0, x2: width, y1: y0, y2: y0 }, svg);
    var rects = items.map(function (it, i) {
      var cls = kind === "money" ? (it.value > 0 ? "up" : (it.value < 0 ? "down" : "nil")) : (it.value > 0 ? tier(it.value / (hi || 1)) : "nil");
      return svgEl("rect", {
        "class": "b " + cls, x: (i * slot + pad0).toFixed(1), y: Math.min(y(it.value), y0).toFixed(1),
        width: bw.toFixed(1), height: Math.max(it.value === 0 ? 1 : 2, Math.abs(y(it.value) - y0)).toFixed(1), rx: 2
      }, svg);
    });
    [0, items.length - 1].forEach(function (i, k) {
      if (k && items.length < 2) { return; }
      var t = svgEl("text", { "class": "ax", x: k ? width - 4 : 4, y: height - 6, "text-anchor": k ? "end" : "start" }, svg);
      t.textContent = items[i].label || items[i].title;
    });
    var summary = summaryLabel + " : total " + fmtValue(kind, total, kind === "money") + ", de " + items[0].title + " à " + items[items.length - 1].title + ".";
    host.setAttribute("data-summary", summary);
    var prev = -1;
    interact(host, svg, items.length, function (px) { return clamp(Math.floor(px / slot), 0, items.length - 1); }, function (i) {
      if (prev >= 0) { rects[prev].classList.remove("hot"); }
      rects[i].classList.add("hot"); prev = i;
      var it = items[i];
      var lines = [{ text: arrow(it.value) + " " + fmtValue(kind, it.value, kind === "money"), tone: kind === "money" ? tone(it.value) : "" }];
      (it.lines || []).forEach(function (l) { lines.push({ text: l }); });
      return { x: i * slot + slot / 2, title: it.title, lines: lines };
    }, function () { if (prev >= 0) { rects[prev].classList.remove("hot"); prev = -1; } }, summary + " Utilisez les flèches pour parcourir les colonnes.");
  }

  function refusalsChart(box) {
    var data = DATA.refusals;
    if (!data || !data.total) {
      empty(box, "Aucun refus du risque sur 30 jours : tous les signaux évalués sont passés.", "/system", "Voir la santé du système");
      return;
    }
    ready(box);
    var head = html("div", "chart-bar", null, box);
    html("span", "chart-big", String(data.total), html("div", "chart-headline", null, head));
    html("span", "chart-delta", "refus sur 30 jours UTC", head);
    var host = html("div", "chart-host", null, box);
    var items = data.days.map(function (d) { return { title: utcDay(d.ts), label: utcDay(d.ts).slice(5), value: d.value, lines: [d.value ? "refus du jour" : "aucun refus"] }; });
    observe(host, function () { renderCols(host, items, "int", "Refus du risque par jour UTC"); });
    if (data.reasons && data.reasons.length) {
      html("div", "sub-title", "Motifs les plus fréquents", box);
      var rh = html("div", "chart-host", null, box);
      renderBars(rh, data.reasons.map(function (r) { return { name: r.name, value: r.value }; }), "int", {});
    }
  }

  function bucketsChart(box) {
    var sets = DATA.buckets || {};
    var names = Object.keys(sets);
    if (!names.length) {
      empty(box, "Aucune découpe disponible : il faut au moins un trade clôturé.", "/trades", "Ouvrir les trades");
      return;
    }
    ready(box);
    var current = names[0];
    var tabs = html("div", "chart-tabs", null, box);
    var group = html("div", "seg", null, tabs);
    group.setAttribute("role", "group"); group.setAttribute("aria-label", "Découpe affichée");
    var buttons = names.map(function (n) {
      var b = html("button", "seg-btn", n, group);
      b.type = "button"; b.setAttribute("aria-pressed", String(n === current));
      b.addEventListener("click", function () { current = n; buttons.forEach(function (x, i) { x.setAttribute("aria-pressed", String(names[i] === current)); }); force(); });
      return b;
    });
    var host = html("div", "chart-host", null, box);
    var alt = html("div", "chart-alt", null, box);
    function draw() {
      var items = sets[current].map(function (b) {
        return { title: b.name, label: b.name, value: b.value, lines: [b.n + " trade(s)", b.wr == null ? "réussite n/a" : "réussite " + Math.round(b.wr * 100) + " %"] };
      });
      renderCols(host, items, "money", "Résultat net par " + current.toLowerCase());
      alt.innerHTML = "";
      tableAlt(alt, "Résultat net par " + current.toLowerCase(), [current, "Résultat net", "Trades"], items.map(function (it) { return [it.title, money(it.value, true), String(sets[current][items.indexOf(it)].n)]; }));
    }
    var force = observe(host, draw);
  }

  /* Donut for any {name, value, tone?} list. */
  function donutGeneric(box) {
    var key = box.getAttribute("data-key");
    var unit = box.getAttribute("data-unit") || "";
    var rows = (DATA[key] || []).filter(function (r) { return r.value > 0; });
    if (!rows.length) {
      empty(box, box.getAttribute("data-empty") || "Rien à répartir pour le moment.", "/system", "Voir le système");
      return;
    }
    ready(box);
    var total = rows.reduce(function (s, r) { return s + r.value; }, 0);
    var shades = [96, 76, 62, 54, 46];
    var toneVar = { ok: "var(--up)", bad: "var(--down)", warn: "var(--warn)" };
    var wrap = html("div", "donut-wrap", null, box);
    var size = 120, r = 44, circ = 2 * Math.PI * r;
    var svg = svgEl("svg", { "class": "donut", viewBox: "0 0 " + size + " " + size, role: "img", style: "width:120px;height:120px" }, html("div", "donut-host", null, wrap));
    svg.setAttribute("aria-label", "Répartition de " + total + " " + unit + " : " + rows.map(function (x) { return x.name + " " + x.value; }).join(", ") + ".");
    svgEl("circle", { "class": "ring-bg", cx: size / 2, cy: size / 2, r: r, "stroke-width": 14 }, svg);
    var cA = svgEl("text", { "class": "c-main", x: size / 2, y: size / 2 + 2, "text-anchor": "middle" }, svg);
    var cB = svgEl("text", { "class": "c-sub", x: size / 2, y: size / 2 + 16, "text-anchor": "middle" }, svg);
    var list = html("ul", "legend", null, wrap);
    var arcs = [], lis = [], acc = 0;
    function idle() { cA.textContent = String(total); cB.textContent = "total"; arcs.forEach(function (a) { a.classList.remove("hot", "dim"); }); lis.forEach(function (x) { x.classList.remove("hot"); }); }
    function focusOn(i) {
      arcs.forEach(function (a, k) { a.classList.toggle("hot", k === i); a.classList.toggle("dim", k !== i); });
      lis.forEach(function (x, k) { x.classList.toggle("hot", k === i); });
      cA.textContent = Math.round(rows[i].value / total * 100) + " %"; cB.textContent = rows[i].name.slice(0, 9);
    }
    rows.forEach(function (row, i) {
      var len = row.value / total * circ, gap = rows.length > 1 ? 2.5 : 0;
      var arc = svgEl("circle", { "class": "arc", cx: size / 2, cy: size / 2, r: r, "stroke-width": 14, "stroke-dasharray": Math.max(0.5, len - gap).toFixed(2) + " " + circ.toFixed(2), "stroke-dashoffset": (-acc).toFixed(2), transform: "rotate(-90 " + size / 2 + " " + size / 2 + ")" }, svg);
      var shade = shades[Math.min(i, shades.length - 1)] + "%";
      arc.style.setProperty("--shade", shade);
      if (row.tone) { arc.style.stroke = toneVar[row.tone]; }
      acc += len; arcs.push(arc);
      var li = html("li", "legend-row", null, list);
      li.tabIndex = 0;
      var sw = html("span", "swatch", null, li);
      sw.style.setProperty("--shade", shade);
      if (row.tone) { sw.style.background = toneVar[row.tone]; }
      html("span", "legend-name", row.name, li);
      html("span", "legend-n num", row.value + " · " + Math.round(row.value / total * 100) + " %", li);
      lis.push(li);
      [arc, li].forEach(function (n) { n.addEventListener("pointerenter", function () { focusOn(i); }); n.addEventListener("pointerleave", idle); });
      li.addEventListener("focus", function () { focusOn(i); });
      li.addEventListener("blur", idle);
    });
    idle();
  }

  function heartbeatsChart(box) {
    var rows = (DATA.heartbeats || []);
    if (!rows.length) { empty(box, "Aucun battement d'EA lisible : le pont n'est pas configuré.", "/risk", "Voir le risque"); return; }
    ready(box);
    renderBars(box, rows.map(function (r) { return { name: r.name, value: r.value, sub: r.ok ? "EA en ligne" : "EA hors ligne", cls: r.ok ? "" : "down" }; }), "sec", { hint: "Âge du dernier battement : plus c'est court, mieux c'est." });
  }
  function latenciesChart(box) {
    var rows = (DATA.latencies || []);
    if (!rows.length) { empty(box, "Aucune latence mesurée : il faut au moins un ordre exécuté.", "/trades", "Voir les trades"); return; }
    ready(box);
    renderBars(box, rows.map(function (r) {
      return { name: r.name, value: r.median == null ? 0 : r.median, alt: r.worst, sub: r.n + " mesure(s)", label: "méd " + fmtValue("num", r.median) + " · pire " + fmtValue("num", r.worst) + " ms" };
    }), "ms", { hint: "Barre pleine : médiane. Estompée : pire cas." });
  }

  /* Replay: entry, stop, targets and close drawn on the server's candles, with a crosshair. */
  function replayChart() {
    var data = DATA.replay;
    var svg = document.querySelector("svg.replay-chart");
    if (!data || !svg || !data.candles || data.candles.length < 2) { return; }
    var host = svg.closest("figure") || svg.parentNode;
    host.style.position = "relative";
    var top = data.top, bottom = data.bottom, span = (data.high - data.low) || 1;
    function y(price) { return bottom - (price - data.low) / span * (bottom - top); }
    var legend = html("ul", "replay-legend", null, host);
    var figcap = host.querySelector("figcaption");
    if (figcap) { host.insertBefore(legend, figcap); }
    (data.levels || []).forEach(function (lv) {
      var py = clamp(y(lv.price), top - 8, bottom + 8);
      var out = lv.price > data.high ? " ↑" : (lv.price < data.low ? " ↓" : "");
      svgEl("line", { "class": "lvl " + lv.kind, x1: 0, x2: data.width, y1: py, y2: py }, svg);
      var tx = svgEl("text", { "class": "lvl-label", x: 4, y: py - 4 }, svg);
      tx.textContent = lv.name + " " + lv.price + out;
      var li = html("li", null, null, legend);
      html("i", lv.kind, null, li);
      html("span", null, lv.name + " " + lv.price + (out ? " (hors cadre)" : ""), li);
    });
    var n = data.candles.length, slot = data.width / n;
    var xh = svgEl("line", { "class": "xh", y1: top, y2: bottom, visibility: "hidden" }, svg);
    var summary = "Bougies et niveaux du trade : " + n + " bougies" + ((data.levels || []).length ? ", " + data.levels.map(function (l) { return l.name + " " + l.price; }).join(", ") : "") + ".";
    interact(host, svg, n, function (px) { return clamp(Math.floor(px / slot), 0, n - 1); }, function (i) {
      var c = data.candles[i], cx = i * slot + slot / 2;
      xh.setAttribute("visibility", "visible"); xh.setAttribute("x1", cx); xh.setAttribute("x2", cx);
      var d = c[4] - c[1];
      return { x: cx, title: utcTime(c[0]), lines: [
        { text: "O " + c[1] + "  H " + c[2] },
        { text: "L " + c[3] + "  C " + c[4] },
        { text: arrow(d) + " " + (d > 0 ? "haussière" : (d < 0 ? "baissière" : "neutre")), tone: tone(d) }
      ] };
    }, function () { xh.setAttribute("visibility", "hidden"); }, summary + " Utilisez les flèches pour parcourir les bougies.");
    svg.removeAttribute("role");
    svg.setAttribute("role", "slider");
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-chart]"), function (box) {
    var kind = box.getAttribute("data-chart");
    if (kind === "equity") { equityChart(box); }
    else if (kind === "pnl-bars") { barsChart(box); }
    else if (kind === "markets") { donutChart(box); }
    else if (kind === "hbars") { hbarsChart(box); }
    else if (kind === "exposure") { exposureChart(box); }
    else if (kind === "strategies") { strategiesChart(box); }
    else if (kind === "gauges") { gaugesChart(box); }
    else if (kind === "refusals") { refusalsChart(box); }
    else if (kind === "buckets") { bucketsChart(box); }
    else if (kind === "donut") { donutGeneric(box); }
    else if (kind === "heartbeats") { heartbeatsChart(box); }
    else if (kind === "latencies") { latenciesChart(box); }
  });
  sparks();
  replayChart();
})();
