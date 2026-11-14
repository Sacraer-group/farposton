(() => {
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
      const token = document.querySelector('meta[name="csrf-token"]')?.content;
      if (token) headers.set('X-CSRF-Token', token);
      options = { ...options, headers };
    }
    return originalFetch(input, options);
  };
  const workspace = document.querySelector("[data-workspace]");
  const compact = matchMedia("(max-width: 900px)");
  const switches = [...document.querySelectorAll("button[data-view]")];

  function updateView() {
    if (!workspace) return;
    const view = compact.matches && workspace.dataset.view === "split"
      ? "list" : workspace.dataset.view;
    switches.forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    });
    document.dispatchEvent(new Event("farposton:layout"));
  }

  switches.forEach(button => button.addEventListener("click", () => {
    workspace.dataset.view = button.dataset.view;
    updateView();
  }));
  compact.addEventListener("change", updateView);
  updateView();

  document.addEventListener("click", event => {
    const button = event.target.closest("[data-map-focus]");
    if (!button || !workspace) return;
    workspace.dataset.view = compact.matches ? "map" : "split";
    updateView();
    requestAnimationFrame(() => {
      document.dispatchEvent(new CustomEvent("farposton:map-focus", {
        detail: { key: button.dataset.mapFocus },
      }));
      if (compact.matches) workspace.scrollIntoView({ block: "start" });
    });
  });

  const choices = [...document.querySelectorAll('input[name="o"][form="compare-form"]')];
  const compareButton = document.querySelector("[data-compare-submit]");
  const count = document.querySelector("[data-selected-count]");
  const hint = document.querySelector("[data-compare-hint]");
  function updateComparison() {
    choices.forEach(input => {
      if (input.closest("[data-saved-offer]")?.hidden) input.checked = false;
    });
    const selected = choices.filter(input => input.checked).length;
    if (!compareButton) return;
    compareButton.disabled = selected === 0 || selected > 5;
    count.textContent = selected;
    hint.textContent = selected === 5
      ? "Выбрано максимум. Снимите отметку, чтобы заменить вариант"
      : selected ? "Расходы, дом и условия — в одной таблице"
        : "Отметьте квартиры, которые хотите сопоставить";
    choices.forEach(input => {
      input.disabled = Boolean(input.closest("[data-saved-offer]")?.hidden) || (!input.checked && selected >= 5);
    });
  }
  choices.forEach(input => input.addEventListener("change", updateComparison));
  document.addEventListener("decisionschange", updateComparison);
  updateComparison();

  document.querySelectorAll("[data-copy-questions]").forEach(button => {
    button.addEventListener("click", async () => {
      const panel = button.closest(".questions-panel");
      const lines = [...panel.querySelectorAll("[data-questions] li")]
        .map(item => item.lastElementChild.textContent.trim());
      const text = lines.map((line, index) => `${index + 1}. ${line}`).join("\n");
      const status = panel.querySelector(".copy-status");
      try {
        await navigator.clipboard.writeText(text);
        status.textContent = "Вопросы скопированы";
      } catch {
        let field = panel.querySelector("textarea");
        if (!field) {
          field = document.createElement("textarea");
          field.className = "copy-fallback";
          field.readOnly = true;
          field.setAttribute("aria-label", "Вопросы для копирования");
          panel.append(field);
        }
        field.value = text;
        field.focus();
        field.select();
        status.textContent = "Скопируйте выделенный текст";
      }
    });
  });

  document.querySelectorAll("img").forEach(img => {
    const unavailable = () => {
      const placeholder = document.createElement("div");
      placeholder.className = "photo-placeholder photo-unavailable";
      placeholder.textContent = "Фотография недоступна в источнике";
      placeholder.setAttribute("role", "img");
      placeholder.setAttribute("aria-label", img.alt || "Фотография недоступна");
      img.replaceWith(placeholder);
    };
    img.addEventListener("error", unavailable, { once: true });
    if (img.complete && img.currentSrc && !img.naturalWidth) unavailable();
  });
})();
