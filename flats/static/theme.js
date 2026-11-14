(() => {
  const root = document.documentElement;
  const system = matchMedia("(prefers-color-scheme: dark)");
  let chosen = null;
  try {
    const saved = localStorage.getItem("bereg.theme");
    if (saved === "light" || saved === "dark") chosen = saved;
  } catch {}

  function apply(theme) {
    root.dataset.theme = theme;
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.setAttribute("aria-pressed", String(theme === "dark"));
      button.hidden = false;
    }
  }
  apply(chosen || (system.matches ? "dark" : "light"));
  system.addEventListener("change", () => {
    if (!chosen) apply(system.matches ? "dark" : "light");
  });
  document.addEventListener("DOMContentLoaded", () => {
    apply(root.dataset.theme);
    document.getElementById("theme-toggle")?.addEventListener("click", () => {
      chosen = root.dataset.theme === "dark" ? "light" : "dark";
      apply(chosen);
      try { localStorage.setItem("bereg.theme", chosen); } catch {}
    });
  });
})();
