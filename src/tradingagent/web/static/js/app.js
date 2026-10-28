/* NexaGold dashboard : progressive enhancement only.
   Every page is complete without this file (tables scroll, selects are native, links work).
   It adds: the theme button, the phone "Plus" sheet, stacked-card labels for wide tables,
   keyboard access to scrolling tables, local-time tooltips, and the styled dropdowns. */
(function () {
  "use strict";

  /* -- theme button -------------------------------------------------------------------
     The theme is already set by the inline script in <head>; here we only toggle, remember
     and keep the accessible state current. aria-pressed is true when the light theme is on. */
  (function theme() {
    var root = document.documentElement;
    var button = document.getElementById("theme-toggle");
    if (!button) { return; }
    function current() { return root.dataset.theme === "light" ? "light" : "dark"; }
    function paint() {
      var light = current() === "light";
      var label = light ? "Activer le thème sombre" : "Activer le thème clair";
      button.setAttribute("aria-pressed", String(light));
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    }
    button.addEventListener("click", function () {
      var next = current() === "light" ? "dark" : "light";
      root.dataset.theme = next;
      try { localStorage.setItem("ta-theme", next); } catch (error) { /* private mode */ }
      paint();
    });
    paint();
  })();

  /* -- navigation sheet (phone) -------------------------------------------------------
     Escape, the scrim and choosing an entry close it; Escape returns focus to the button. */
  (function drawer() {
    var button = document.getElementById("menu-toggle");
    var nav = document.getElementById("side-nav");
    if (!button || !nav) { return; }
    function isOpen() { return document.body.dataset.drawer === "open"; }
    var behind = document.querySelectorAll("header.topbar, main, footer");
    function setBehind(inert) {
      for (var i = 0; i < behind.length; i++) {
        if (inert) { behind[i].setAttribute("inert", ""); } else { behind[i].removeAttribute("inert"); }
      }
    }
    function open() {
      document.body.dataset.drawer = "open";
      button.setAttribute("aria-expanded", "true");
      setBehind(true);
      // The sheet is still visibility:hidden for the first frames of its transition.
      window.setTimeout(function () {
        var first = nav.querySelector("a");
        if (first && isOpen()) { first.focus(); }
      }, 60);
    }
    function close(restoreFocus) {
      delete document.body.dataset.drawer;
      button.setAttribute("aria-expanded", "false");
      setBehind(false);
      if (restoreFocus) { button.focus(); }
    }
    // Tab stays inside the sheet and the button that closes it.
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Tab" || !isOpen()) { return; }
      var stops = Array.prototype.slice.call(nav.querySelectorAll("a[href]"));
      stops.push(button);
      var index = stops.indexOf(document.activeElement);
      var next = event.shiftKey ? index - 1 : index + 1;
      if (index === -1) { next = 0; }
      if (next < 0) { next = stops.length - 1; }
      if (next >= stops.length) { next = 0; }
      event.preventDefault();
      stops[next].focus();
    });
    button.addEventListener("click", function () { if (isOpen()) { close(true); } else { open(); } });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && isOpen()) { close(true); }
    });
    var scrim = document.querySelector("[data-drawer-close]");
    if (scrim) { scrim.addEventListener("click", function () { close(false); }); }
    nav.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest("a")) { close(false); }
    });
    // Growing past the phone breakpoint leaves the sheet state behind otherwise.
    window.addEventListener("resize", function () {
      if (window.innerWidth > 767 && isOpen()) { close(false); }
    });
  })();

  /* -- tables ------------------------------------------------------------------------- */
  function headerLabels(table) {
    var rows = table.tHead ? table.tHead.rows : [];
    if (!rows.length) { return null; }
    var cells = rows[rows.length - 1].cells;
    var labels = [];
    for (var i = 0; i < cells.length; i++) { labels.push(cells[i].textContent.trim()); }
    return labels;
  }

  function labelRow(row, labels) {
    var column = 0;
    for (var i = 0; i < row.cells.length; i++) {
      var cell = row.cells[i];
      if (cell.colSpan > 1 || cell.classList.contains("empty")) { column += cell.colSpan; continue; }
      if (labels[column] && !cell.hasAttribute("data-label")) {
        cell.setAttribute("data-label", labels[column]);
      }
      column += 1;
    }
  }

  function headingOf(wrap) {
    var node = wrap.previousElementSibling;
    while (node && node.tagName !== "H2" && node.tagName !== "H3") {
      node = node.previousElementSibling;
    }
    return node ? node.textContent.trim() : "";
  }

  function enhanceTable(wrap) {
    var table = wrap.querySelector("table");
    if (!table || wrap.dataset.enhanced === "1") { return; }
    wrap.dataset.enhanced = "1";
    var labels = headerLabels(table);
    if (!labels) { wrap.classList.add("is-kv"); return; }
    if (labels.length < 3) { return; }
    wrap.classList.add("is-stack");
    Array.prototype.forEach.call(table.tBodies, function (body) {
      Array.prototype.forEach.call(body.rows, function (row) { labelRow(row, labels); });
      // Rows pushed later (live alerts) get their labels too.
      new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
          Array.prototype.forEach.call(mutation.addedNodes, function (node) {
            if (node.tagName === "TR") { labelRow(node, labels); }
          });
        });
      }).observe(body, { childList: true });
    });
  }

  // A scrolling area must be reachable from the keyboard, and only then.
  function markScrollable() {
    Array.prototype.forEach.call(document.querySelectorAll(".table-wrap"), function (wrap) {
      if (wrap.scrollWidth > wrap.clientWidth + 1) {
        wrap.setAttribute("tabindex", "0");
        wrap.setAttribute("role", "region");
        var name = headingOf(wrap);
        if (name) { wrap.setAttribute("aria-label", name); }
      } else {
        wrap.removeAttribute("tabindex");
        wrap.removeAttribute("role");
        wrap.removeAttribute("aria-label");
      }
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".table-wrap"), enhanceTable);
  markScrollable();
  window.addEventListener("resize", markScrollable);
  window.addEventListener("load", markScrollable);

  /* -- local time ---------------------------------------------------------------------
     Data and display stay UTC. The reader's own clock is offered as a tooltip only. */
  (function localTime() {
    var format;
    try {
      format = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "medium", timeZoneName: "short"
      });
    } catch (error) { return; }
    Array.prototype.forEach.call(document.querySelectorAll("time[datetime]"), function (node) {
      var date = new Date(node.getAttribute("datetime"));
      if (!isNaN(date.getTime())) { node.title = "Heure locale : " + format.format(date); }
    });
  })();

  /* -- styled dropdowns -----------------------------------------------------------------
     The native <select> stays the control (name, value, submission); it is only hidden. */
  var CHEVRON =
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<use href="#i-chevron-down"/></svg>';

  function enhanceSelect(select) {
    if (select.dataset.customReady === "1") { return; }
    select.dataset.customReady = "1";

    var wrapper = document.createElement("div");
    wrapper.className = "custom-select";
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    select.classList.add("custom-select-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    var base = select.id || "select";
    var buttonId = base + "-button";
    var listId = base + "-list";

    var button = document.createElement("button");
    button.type = "button";
    button.id = buttonId;
    button.className = "custom-select-button";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", listId);

    var value = document.createElement("span");
    value.className = "custom-select-value";
    button.appendChild(value);
    var chevron = document.createElement("span");
    chevron.className = "custom-select-chevron";
    chevron.innerHTML = CHEVRON;
    button.appendChild(chevron);

    var list = document.createElement("ul");
    list.className = "custom-select-list";
    list.id = listId;
    list.setAttribute("role", "listbox");
    list.hidden = true;

    var options = [];
    Array.prototype.forEach.call(select.options, function (option, index) {
      var item = document.createElement("li");
      item.className = "custom-select-option";
      item.id = listId + "-option-" + index;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(option.selected));
      item.dataset.value = option.value;
      item.textContent = option.textContent;
      list.appendChild(item);
      options.push(item);
    });

    var label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
    if (label) {
      if (!label.id) { label.id = base + "-label"; }
      button.setAttribute("aria-labelledby", label.id + " " + buttonId);
      label.htmlFor = buttonId;
    }

    wrapper.appendChild(button);
    wrapper.appendChild(list);

    var active = Math.max(0, select.selectedIndex);

    function paintValue() {
      var option = select.options[select.selectedIndex];
      value.textContent = option ? option.textContent : "";
      for (var i = 0; i < options.length; i++) {
        options[i].setAttribute("aria-selected", String(i === select.selectedIndex));
      }
    }
    function isOpen() { return !list.hidden; }
    function setActive(index, scroll) {
      if (index < 0) { index = 0; }
      if (index > options.length - 1) { index = options.length - 1; }
      active = index;
      for (var i = 0; i < options.length; i++) {
        if (i === active) { options[i].classList.add("active"); }
        else { options[i].classList.remove("active"); }
      }
      if (options[active]) {
        button.setAttribute("aria-activedescendant", options[active].id);
        if (scroll && options[active].scrollIntoView) {
          options[active].scrollIntoView({ block: "nearest" });
        }
      }
    }
    function open() {
      list.hidden = false;
      wrapper.classList.add("custom-select-open");
      button.setAttribute("aria-expanded", "true");
      setActive(select.selectedIndex, true);
    }
    function close(focusButton) {
      list.hidden = true;
      wrapper.classList.remove("custom-select-open");
      button.setAttribute("aria-expanded", "false");
      button.removeAttribute("aria-activedescendant");
      if (focusButton) { button.focus(); }
    }
    function choose(index) {
      if (index < 0 || index >= select.options.length) { return; }
      select.selectedIndex = index;
      paintValue();
      close(true);
    }

    button.addEventListener("click", function () { if (isOpen()) { close(false); } else { open(); } });
    button.addEventListener("keydown", function (event) {
      var key = event.key;
      if (!isOpen()) {
        if (key === "ArrowDown" || key === "ArrowUp" || key === "Enter" || key === " ") {
          event.preventDefault();
          open();
        }
        return;
      }
      if (key === "Escape") { event.preventDefault(); close(true); }
      else if (key === "ArrowDown") { event.preventDefault(); setActive(active + 1, true); }
      else if (key === "ArrowUp") { event.preventDefault(); setActive(active - 1, true); }
      else if (key === "Home") { event.preventDefault(); setActive(0, true); }
      else if (key === "End") { event.preventDefault(); setActive(options.length - 1, true); }
      else if (key === "Enter" || key === " ") { event.preventDefault(); choose(active); }
      else if (key === "Tab") { close(false); }
    });
    list.addEventListener("click", function (event) {
      var item = event.target.closest ? event.target.closest(".custom-select-option") : null;
      if (item) { choose(options.indexOf(item)); }
    });
    list.addEventListener("mousemove", function (event) {
      var item = event.target.closest ? event.target.closest(".custom-select-option") : null;
      if (item) { setActive(options.indexOf(item), false); }
    });
    document.addEventListener("click", function (event) {
      if (isOpen() && !wrapper.contains(event.target)) { close(false); }
    });
    select.addEventListener("change", paintValue);
    paintValue();
  }

  Array.prototype.forEach.call(document.querySelectorAll("select[data-custom-select]"), enhanceSelect);
})();
