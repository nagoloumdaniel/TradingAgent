/* Bouton « Tester l'appel IA » : une vraie requête au modèle, le résultat en clair. */
(function () {
  var button = document.getElementById("ai-test-btn");
  var out = document.getElementById("ai-test-result");
  if (!button || !out) { return; }
  var label = button.querySelector("span");
  var idle = label.textContent;

  function show(kind, text) {
    out.textContent = text;
    out.dataset.state = kind;
    out.className = kind === "ok" ? "ai-test-ok" : kind === "bad" ? "ai-test-bad" : "muted";
  }

  button.addEventListener("click", function () {
    if (button.disabled) { return; }
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    show("wait", "Appel en cours…");
    fetch(button.dataset.url, { method: "POST", credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(function (r) {
        var meta = (r.model ? r.model : "") + (r.latency_ms != null ? " · " + r.latency_ms + " ms" : "");
        show(r.ok ? "ok" : "bad", (r.ok ? "Succès : " : "Échec : ") + r.detail + (meta ? " (" + meta + ")" : ""));
      })
      .catch(function (error) {
        show("bad", "Échec : le tableau de bord n'a pas répondu (" + error.message + ").");
      })
      .then(function () {
        button.disabled = false;
        button.classList.remove("is-loading");
        button.removeAttribute("aria-busy");
        label.textContent = idle;
      });
  });
})();
