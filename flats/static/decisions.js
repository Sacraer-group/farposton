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

  function resetSkipped() {
    const history = read();
    const latest = new Map(history.map(entry => [JSON.stringify(entry.slice(0, 2)), entry[2]]));
    const skipped = new Set([...latest].filter(([, action]) => action === "skip").map(([id]) => id));
    if (!skipped.size) return;
    // Remove the whole history of skipped offers so older saves cannot reappear.
    localStorage.setItem(key, JSON.stringify(history.filter(entry => !skipped.has(JSON.stringify(entry.slice(0, 2))))));
  }

  function current() {
    return [...new Map(read().map(entry => [JSON.stringify(entry.slice(0, 2)), entry])).values()];
  }

  function refresh() {
    const decisions = new Map(current().map(([source, id, action]) => [JSON.stringify([source, id]), action]));
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
          const label = button.querySelector("[data-save-label]");
          if (label) {
            label.dataset.original ||= label.textContent;
            label.textContent = saved ? "В избранном" : label.dataset.original;
          }
          button.classList.toggle("button-saved", saved);
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
      const savedCount = document.querySelectorAll("[data-saved-offer]:not([hidden])").length;
      compare.hidden = savedCount === 0;
      document.getElementById("favorites-content").hidden = compare.hidden;
      document.getElementById("favorites-empty").hidden = !compare.hidden;
      document.getElementById("favorites-count").textContent = savedCount;
    }
    const count = document.getElementById("search-count");
    if (count) {
      const visible = document.querySelectorAll("[data-card-offers]:not([hidden])").length;
      count.textContent = visible;
      document.getElementById("search-short").hidden = visible >= 3;
    }
    const related = document.getElementById("house-related");
    if (related) {
      document.getElementById("house-related-empty").hidden = Boolean(related.querySelector("[data-card-offers]:not([hidden])"));
    }
    const cards = new Map([...document.querySelectorAll("[data-listing-key]")]
      .map(card => [card.dataset.listingKey, card]));
    document.querySelectorAll("[data-listing-ref]").forEach(link => {
      link.hidden = cards.get(link.dataset.listingRef)?.hidden || false;
    });
    const unlocated = document.getElementById("map-unlocated");
    if (unlocated) {
      const count = unlocated.querySelectorAll("[data-listing-ref]:not([hidden])").length;
      unlocated.hidden = count === 0;
      document.getElementById("unlocated-count").textContent = count;
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

  window.FlatsDecisions = { decide, undo, resetSkipped, current, refresh, reportError };
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
