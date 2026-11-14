const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const KEY = "farposton.decisions.v1";
const scripts = Object.fromEntries(["decisions", "swipe"].map(name => [
  name, fs.readFileSync(path.join(__dirname, "../flats/static", `${name}.js`), "utf8"),
]));

function storage(raw = null) {
  return {
    raw, writes: 0, failRead: false, failWrite: false,
    getItem(key) {
      assert.equal(key, KEY);
      if (this.failRead) throw new Error("Storage read failed");
      return this.raw;
    },
    setItem(key, value) {
      assert.equal(key, KEY);
      this.writes += 1;
      if (this.failWrite) throw new Error("Storage write failed");
      this.raw = value;
    },
  };
}

const history = store => JSON.parse(store.raw || "[]");

function events(object = {}) {
  const listeners = new Map();
  return Object.assign(object, {
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    dispatchEvent(event) {
      for (const listener of listeners.get(event.type) || []) listener(event);
    },
  });
}

function card(ids, swipe = false) {
  const label = { hidden: true };
  return {
    hidden: false, style: {}, label,
    dataset: { source: "farpost", sourceId: ids[0], cardOffers: JSON.stringify(ids.map(id => ["farpost", id])) },
    classList: { contains: name => swipe && name === "swipe-card" },
    querySelector: selector => selector === "[data-saved-label]" ? label : null,
    closest: () => null,
    contains(target) { return target === this; },
    setPointerCapture() {},
    releasePointerCapture() {},
  };
}

// Only the DOM surface used by the scripts; assertions exercise the real scripts.
function page(store, cards = [], swipe = false) {
  const nodes = {
    "decisions-error": { hidden: true, textContent: "" },
    "search-count": {}, "search-short": {}, empty: {},
  };
  for (const id of ["save", "skip", "details", "undo", "resetSkips"]) nodes[id] = events({ disabled: true });
  nodes.stack = events({ querySelectorAll: () => cards.filter(c => !c.hidden && c.classList.contains("swipe-card")) });
  const document = events({
    getElementById: id => nodes[id] || null,
    querySelectorAll(selector) {
      if (selector === "[data-reset-skips]") return [nodes.resetSkips];
      if (selector === "[data-card-offers]") return cards;
      if (selector === "[data-card-offers]:not([hidden])") return cards.filter(c => !c.hidden);
      if (["[data-decision-form]", "[data-saved-offer]", "[data-listing-key]", "[data-listing-ref]"].includes(selector)) return [];
      throw new Error(`Unexpected selector: ${selector}`);
    },
  });
  const window = events();
  const timers = [];
  const context = vm.createContext({
    window, document, localStorage: store, Event,
    FormData: class { constructor(form) { this.values = form.values; } get(key) { return this.values[key] ?? null; } },
    matchMedia: () => ({ matches: true }),
    setTimeout: callback => timers.push(callback),
  });
  vm.runInContext(scripts.decisions, context);
  if (swipe) vm.runInContext(scripts.swipe, context);
  return {
    window, document, nodes, decisions: window.FlatsDecisions,
    flush() { while (timers.length) timers.shift()(); },
  };
}

function submit(view, action = "save") {
  let prevented = false;
  const form = { values: { source: "farpost", source_id: "one" }, closest() { return this; } };
  view.document.dispatchEvent({
    type: "submit", target: form, submitter: { value: action },
    preventDefault() { prevented = true; },
  });
  assert.equal(prevented, true);
}

test("independent browsers keep separate favorites, including after reload", () => {
  const a = storage(), b = storage();
  page(a).decisions.decide("farpost", "one", "save");
  const saved = card(["one"]), fresh = card(["one"]);
  page(a, [saved]);
  page(b, [fresh]);
  assert.equal(saved.label.hidden, false);
  assert.equal(fresh.label.hidden, true);
  assert.deepEqual(history(b), []);
  assert.deepEqual(history(a), [["farpost", "one", "save"]]);
});

test("tabs reread shared storage before writing and refresh on storage/pageshow", () => {
  const store = storage(), first = card(["one"]);
  const a = page(store, [first]), b = page(store);
  a.decisions.decide("farpost", "one", "save");
  b.decisions.decide("farpost", "two", "skip");
  assert.deepEqual(history(store), [["farpost", "one", "save"], ["farpost", "two", "skip"]]);
  a.window.dispatchEvent({ type: "storage", key: KEY });
  assert.equal(first.label.hidden, false);
  b.decisions.decide("farpost", "one", "skip");
  a.window.dispatchEvent({ type: "pageshow" });
  assert.equal(first.hidden, true);
});

test("undo after reload restores the previous action and empty undo does not write", () => {
  const store = storage(), a = page(store);
  a.decisions.undo();
  assert.equal(store.writes, 0);
  a.decisions.decide("farpost", "one", "save");
  a.decisions.decide("farpost", "one", "skip");
  const reloaded = page(store);
  reloaded.decisions.undo();
  assert.deepEqual(history(store), [["farpost", "one", "save"]]);
  reloaded.decisions.undo();
  assert.deepEqual(history(store), []);
  const writes = store.writes;
  reloaded.decisions.undo();
  assert.equal(store.writes, writes);
});

test("advisor gets only the current local decisions without rewriting the undo journal", () => {
  const store = storage(), view = page(store);
  view.decisions.decide("farpost", "one", "save");
  view.decisions.decide("farpost", "one", "skip");
  view.decisions.decide("farpost", "two", "save");
  const raw = store.raw;
  assert.deepEqual(JSON.parse(JSON.stringify(view.decisions.current())), [["farpost", "one", "skip"], ["farpost", "two", "save"]]);
  assert.equal(store.raw, raw);
});

for (const raw of ["{", "null", "{}", "[null]", '[["farpost","one"]]', '[["farpost","one","like"]]', '[["farpost",7,"save"]]']) {
  test(`invalid stored data is reported and never overwritten: ${raw}`, () => {
    const store = storage(raw), view = page(store);
    let changes = 0;
    view.document.addEventListener("decisionschange", () => changes++);
    submit(view);
    assert.throws(() => view.decisions.undo());
    assert.equal(store.raw, raw);
    assert.equal(store.writes, 0);
    assert.equal(view.nodes["decisions-error"].hidden, false);
    assert.equal(changes, 0);
  });
}

for (const failure of ["failRead", "failWrite"]) {
  test(`${failure} preserves saved data and reports failure without a success update`, () => {
    const raw = '[["farpost","one","save"]]';
    const store = storage(raw), view = page(store);
    let changes = 0;
    view.document.addEventListener("decisionschange", () => changes++);
    store[failure] = true;
    submit(view, "skip");
    assert.throws(() => view.decisions.undo());
    assert.equal(store.raw, raw);
    assert.equal(view.nodes["decisions-error"].hidden, false);
    assert.equal(changes, 0);
    store[failure] = false;
    submit(view, "skip");
    assert.deepEqual(history(store), [["farpost", "one", "save"], ["farpost", "one", "skip"]]);
    assert.equal(view.nodes["decisions-error"].hidden, true);
    assert.equal(changes, 1);
  });
}

test("a saved group member wins over a skip, and clearing storage restores cards", () => {
  const store = storage('[["farpost","one","skip"],["farpost","two","save"]]');
  const listed = card(["one", "two"]), swiped = card(["one", "two"], true);
  const view = page(store, [listed, swiped]);
  assert.equal(listed.hidden, false);
  assert.equal(listed.label.hidden, false);
  assert.equal(swiped.hidden, true);
  view.decisions.decide("farpost", "two", "skip");
  view.decisions.refresh();
  assert.equal(listed.hidden, true);
  view.decisions.undo();
  view.decisions.refresh();
  assert.equal(listed.hidden, false);
  store.raw = null;
  view.window.dispatchEvent({ type: "storage", key: null });
  assert.equal(listed.label.hidden, true);
  assert.equal(swiped.hidden, false);
});

test("swipe saves once during animation and undo works after reload", () => {
  const store = storage(), saved = card(["one"], true);
  const view = page(store, [saved], true);
  view.nodes.save.dispatchEvent({ type: "click" });
  view.nodes.save.dispatchEvent({ type: "click" });
  assert.deepEqual(history(store), [["farpost", "one", "save"]]);
  view.flush();
  assert.equal(saved.hidden, true);
  const restored = card(["one"], true), reloaded = page(store, [restored], true);
  assert.equal(restored.hidden, true);
  reloaded.nodes.undo.dispatchEvent({ type: "click" });
  assert.equal(restored.hidden, false);
  assert.deepEqual(history(store), []);
});

test("a drag cannot save the next card when another tab hides the dragged card", () => {
  const store = storage(), next = card(["next"], true), dragged = card(["dragged"], true);
  const view = page(store, [next, dragged], true);
  view.nodes.stack.dispatchEvent({ type: "pointerdown", target: dragged, clientX: 0, pointerId: 1 });
  page(store).decisions.decide("farpost", "dragged", "save");
  view.window.dispatchEvent({ type: "storage", key: KEY });
  view.nodes.stack.dispatchEvent({ type: "pointerup", target: dragged, clientX: 150 });
  view.flush();
  assert.deepEqual(history(store), [["farpost", "dragged", "save"]]);
  assert.equal(next.hidden, false);
});


test("reset skips restores skipped cards, preserves favorites and persists after reload", () => {
  const store = storage(JSON.stringify([
    ["farpost", "one", "save"], ["farpost", "one", "skip"],
    ["farpost", "two", "skip"], ["farpost", "saved", "save"],
  ]));
  const skipped = card(["one"], true), favorite = card(["saved"], true);
  const view = page(store, [skipped, favorite], true);
  assert.equal(skipped.hidden, true);
  assert.equal(view.nodes.resetSkips.disabled, false);
  view.nodes.resetSkips.dispatchEvent({ type: "click" });
  assert.deepEqual(history(store), [["farpost", "saved", "save"]]);
  assert.equal(skipped.hidden, false);
  assert.equal(favorite.hidden, true);
  assert.equal(view.nodes.resetSkips.disabled, true);
  const reloaded = card(["one"], true);
  page(store, [reloaded], true);
  assert.equal(reloaded.hidden, false);
});

test("reset skips does not write when empty and preserves data if storage fails", () => {
  const store = storage(), view = page(store);
  view.decisions.resetSkipped();
  assert.equal(store.writes, 0);
  store.raw = '[["farpost","one","skip"],["farpost","saved","save"]]';
  const original = store.raw;
  for (const failure of ["failRead", "failWrite"]) {
    store[failure] = true;
    assert.throws(() => view.decisions.resetSkipped());
    assert.equal(store.raw, original);
    store[failure] = false;
  }
});

test("swipe reset is blocked during an animation and reports a failed write", () => {
  const store = storage(), view = page(store, [card(["one"], true)], true);
  view.nodes.skip.dispatchEvent({type: "click"});
  view.nodes.resetSkips.dispatchEvent({type: "click"});
  assert.deepEqual(history(store), [["farpost", "one", "skip"]]);
  view.flush();
  assert.equal(view.nodes.empty.hidden, false);
  store.failWrite = true;
  view.nodes.resetSkips.dispatchEvent({type: "click"});
  assert.equal(view.nodes["decisions-error"].hidden, false);
  assert.equal(view.nodes.empty.hidden, false);
  store.failWrite = false;
  view.nodes.resetSkips.dispatchEvent({type: "click"});
  assert.equal(view.nodes.empty.hidden, true);
});
