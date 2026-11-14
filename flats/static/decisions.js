(() => {
  const key = "farposton.decisions.v1";
  const error = document.getElementById("decisions-error");

  function read() {
    const history = JSON.parse(localStorage.getItem(key) || "[]");
    if (!Array.isArray(history) || !history.every(entry =>
      Array.isArray(entry) && entry.length === 3 &&
      typeof entry[0] === "string" && entry[0].length > 0 &&
      typeof entry[1] === "string" && entry[1].length > 0 &&
      (entry[2] === "save" || entry[2] === "skip"))) {
      throw new Error("Invalid saved decisions");
    }
    return history;
  }

  function decide(source, sourceId, action) {
    if (typeof source !== "string" || !source || typeof sourceId !== "string" ||
        !sourceId || !["save", "skip"].includes(action)) {
      throw new Error("Invalid decision");
    }
    const history = read();
    history.push([source, sourceId, action]);
    localStorage.setItem(key, JSON.stringify(history));
  }

  function undo() {
    const history = read();
    if (!history.length) return;
    history.pop();
    localStorage.setItem(key, JSON.stringify(history));
  }

  function refresh() {
    const history = read();
    const decisions = new Map(history.map(([source, id, action]) => [JSON.stringify([source, id]), action]));
    document.querySelectorAll("[data-card-offers]").forEach(card => {
      const actions = JSON.parse(card.dataset.cardOffers).map(offer => decisions.get(JSON.stringify(offer)));
      const saved = actions.includes("save");
      card.hidden = saved ? card.classList.contains("swipe-card") : actions.includes("skip");
      const label = card.querySelector("[data-saved-label]");
      if (label) label.hidden = !saved;
    });
    document.querySelectorAll("[data-decision-form]").forEach(form => {
      const data = new FormData(form);
      const saved = decisions.get(JSON.stringify([data.get("source"), data.get("source_id")])) === "save";
      form.querySelectorAll("button[name=action]").forEach(button => {
        button.disabled = button.value === "save" && saved;
        if (button.value === "save") {
          button.dataset.saveLabel ||= button.textContent;
          button.textContent = saved ? "В избранном" : button.dataset.saveLabel;
        }
      });
    });
    document.querySelectorAll("[data-saved-offer]").forEach(element => {
      element.hidden = decisions.get(JSON.stringify(JSON.parse(element.dataset.savedOffer))) !== "save";
      const checkbox = element.querySelector("input[name=o]");
      if (checkbox) {
        checkbox.disabled = element.hidden;
        if (element.hidden) checkbox.checked = false;
      }
    });
    const compare = document.getElementById("compare-form");
    if (compare) {
      compare.hidden = !compare.querySelector("[data-saved-offer]:not([hidden])");
      document.getElementById("favorites-empty").hidden = !compare.hidden;
    }
    const count = document.getElementById("search-count");
    if (count) {
      const visible = document.querySelectorAll("[data-card-offers]:not([hidden])").length;
      count.textContent = visible;
      document.getElementById("search-short").hidden = visible >= 3;
    }
    error.hidden = true;
    document.dispatchEvent(new Event("decisionschange"));
  }

  function reportError() {
    error.textContent = "Не удалось прочитать или сохранить избранное. Проверьте, разрешено ли хранение данных сайта в браузере. Сохранения не изменены.";
    error.hidden = false;
  }

  function sync() {
    try { refresh(); } catch { reportError(); }
  }

  window.FlatsDecisions = { decide, undo, refresh, reportError };
  document.addEventListener("submit", event => {
    const form = event.target.closest("[data-decision-form]");
    if (!form) return;
    event.preventDefault();
    const data = new FormData(form);
    try {
      decide(data.get("source"), data.get("source_id"), event.submitter?.value);
      refresh();
    } catch { reportError(); }
  });
  window.addEventListener("storage", event => {
    if (event.key === key || event.key === null) sync();
  });
  window.addEventListener("pageshow", sync);
  sync();
})();
