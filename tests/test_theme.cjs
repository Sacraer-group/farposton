const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../flats/static/theme.js"), "utf8");

function page({ dark = false, storage = new Map(), blocked = false } = {}) {
  const root = { dataset: {} }, events = {}, clicks = {};
  let ready = false, systemChanged;
  const button = {
    hidden: true,
    setAttribute(key, value) { this[key] = value; },
    addEventListener(event, callback) { clicks[event] = callback; },
  };
  const system = {
    matches: dark,
    addEventListener(_event, callback) { systemChanged = callback; },
  };
  vm.runInNewContext(source, {
    document: {
      documentElement: root,
      getElementById: () => ready ? button : null,
      addEventListener(event, callback) { events[event] = callback; },
    },
    localStorage: {
      getItem(key) { if (blocked) throw Error("Storage blocked"); return storage.get(key) || null; },
      setItem(key, value) { if (blocked) throw Error("Storage blocked"); storage.set(key, value); },
    },
    matchMedia: () => system,
  });
  const beforeReady = root.dataset.theme;
  ready = true;
  events.DOMContentLoaded();
  return {
    root, button, beforeReady,
    click() { clicks.click(); },
    systemChange(value) { system.matches = value; systemChanged(); },
  };
}

test("system theme is applied before the body is ready and tracks system changes", () => {
  const view = page({ dark: true });
  assert.equal(view.beforeReady, "dark");
  assert.equal(view.button.hidden, false);
  assert.equal(view.button["aria-pressed"], "true");
  view.systemChange(false);
  assert.equal(view.root.dataset.theme, "light");
  assert.equal(view.button["aria-pressed"], "false");
});

test("theme choice survives reloads without touching favorites storage", () => {
  const storage = new Map([["favorites", "keep"]]);
  const view = page({ storage });
  view.click();
  assert.equal(view.root.dataset.theme, "dark");
  assert.equal(storage.get("bereg.theme"), "dark");
  assert.equal(storage.get("favorites"), "keep");
  const reloaded = page({ storage });
  assert.equal(reloaded.beforeReady, "dark");
  reloaded.click();
  assert.equal(storage.get("bereg.theme"), "light");
});

test("explicit preference takes priority over subsequent system changes", () => {
  const storage = new Map([["bereg.theme", "light"]]);
  const view = page({ storage, dark: true });
  view.systemChange(true);
  assert.equal(view.root.dataset.theme, "light");
  view.click();
  view.systemChange(false);
  assert.equal(view.root.dataset.theme, "dark");
});

test("invalid preference falls back to the system theme", () => {
  const view = page({ dark: true, storage: new Map([["bereg.theme", "invalid"]]) });
  assert.equal(view.root.dataset.theme, "dark");
});

test("blocked browser storage does not prevent theme switching", () => {
  const view = page({ blocked: true });
  view.click();
  assert.equal(view.root.dataset.theme, "dark");
  view.click();
  assert.equal(view.root.dataset.theme, "light");
});
