const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../flats/static/map.js"), "utf8");
const sdkSource = fs.readFileSync(path.join(__dirname, "../flats/static/map-sdk.js"), "utf8");

function element(dataset = {}) {
  const listeners = new Map();
  return {
    dataset, style: {}, hidden: false, children: [], textContent: "", isConnected: true,
    append(...children) { this.children.push(...children); },
    setAttribute(name, value) { this[name] = value; },
    focus() { this.focused = true; },
    remove() { this.removed = true; },
    contains(node) { return node === this || this.children.some(child => child.contains?.(node)); },
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
    fire(type, detail, event = {}) { for (const callback of listeners.get(type) || []) callback({ type, detail, ...event }); },
  };
}

function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }

function mapPage({ hidden = [], sdk = true, mapKey = "test-map-key", fail = false, mutateLinks,
  markerBounds, mapBounds, popupBounds, viewport = { width: 320, height: 600 } } = {}) {
  const cards = ["a", "b", "c"].map(key => ({ dataset: { listingKey: key }, hidden: hidden.includes(key) }));
  const links = cards.map((card, index) => Object.assign(element({
    key: card.dataset.listingKey, lat: "43.1", lon: String(131.9 + index / 100),
    houseId: index < 2 ? "7" : "9", houseUrl: index < 2 ? "/house/7" : "/house/9",
    price: `${index + 1}0 000 ₽`, address: index < 2 ? "Дом 7" : "Дом 9",
  }), { href: `https://app.test/flat/${card.dataset.listingKey}` }));
  mutateLinks?.(links);
  const container = Object.assign(element(), { clientWidth: 500, clientHeight: 300,
    getBoundingClientRect: () => mapBounds });
  const fallback = element(), reset = element(), status = element(), shell = element();
  shell.querySelector = selector => ({
    "[data-map]": container, ".map-data": fallback,
    "[data-map-reset]": reset, "[data-map-status]": status,
  })[selector];
  shell.querySelectorAll = () => links;
  const document = Object.assign(element(), {
    querySelector: () => ({ content: mapKey }),
    querySelectorAll: selector => selector === ".map-shell" ? [shell] : cards,
    createElement() {
      const node = element();
      node.getBoundingClientRect = () => node.className === "map-price-pill" ? markerBounds : popupBounds;
      return node;
    },
  });
  const maps = [], markers = [], timers = [], frames = [];
  const mapgl = {
    Map: class {
      constructor(container, options) {
        if (fail) throw Error("WebGL unavailable");
        Object.assign(this, element(), { container, options });
        maps.push(this);
      }
      on(type, callback) { this.addEventListener(type, callback); return this; }
      invalidateSize() {}
      fitBounds(bounds, options) { this.bounds = bounds; this.fitOptions = options; }
      setCenter(center, options) { this.center = center; this.centerOptions = options; }
      setZoom(zoom, options) { this.zoom = zoom; this.zoomOptions = options; }
    },
    HtmlMarker: class {
      constructor(map, options) { Object.assign(this, { map, options, destroyed: false }); markers.push(this); }
      destroy() { this.destroyed = true; }
    },
  };
  vm.runInNewContext(sdkSource + "\n" + source, {
    document, window: { mapgl: sdk ? mapgl : undefined, innerWidth: viewport.width, innerHeight: viewport.height }, URL,
    location: { href: "https://app.test/", origin: "https://app.test" },
    setTimeout(callback, delay) { const timer = { callback, delay }; timers.push(timer); return timer; },
    clearTimeout(timer) { if (timer) timer.cleared = true; },
    requestAnimationFrame(callback) { frames.push(callback); },
  });
  return {
    cards, links, container, fallback, reset, status, shell, maps, markers, timers, document,
    visibleMarkers() { return markers.filter(marker => !marker.destroyed && marker.options.html.className === "map-price-pill"); },
    popup() { return markers.findLast(marker => !marker.destroyed && marker.options.html.className === "map-popup"); },
    refresh() { document.fire("decisionschange"); },
    frame() { frames.splice(0).forEach(callback => callback()); },
  };
}

test("native 2GIS map uses the browser key and longitude/latitude order", () => {
  const view = mapPage();
  assert.equal(view.maps.length, 1);
  assert.equal(view.maps[0].options.key, "test-map-key");
  assert.deepEqual(Array.from(view.maps[0].options.center), [131.9, 43.1]);
  assert.equal(view.maps[0].options.lang, "ru");
  assert.equal(view.maps[0].options.disableZoomOnScroll, true);
  assert.equal(view.shell.dataset.mapState, "loading");
  view.maps[0].fire("idle");
  assert.equal(view.shell.dataset.mapState, "ready");
  assert.equal(view.timers[0].cleared, true);
});

test("skipping and undoing a listing updates grouped markers, popup links and counts", () => {
  const view = mapPage();
  view.maps[0].fire("idle");
  assert.equal(view.visibleMarkers().length, 2);
  assert.equal(view.visibleMarkers()[0].options.html.textContent, "2 квартиры");
  assert.match(view.status.textContent, /Домов на карте: 2 · квартир: 3/);
  const oldMarker = view.visibleMarkers()[0];
  view.cards[0].hidden = true;
  view.refresh();
  assert.equal(oldMarker.destroyed, true);
  const house = view.visibleMarkers()[0];
  assert.equal(house.options.html.textContent, "20 000 ₽");
  house.options.html.fire("click");
  assert.deepEqual(descendants(view.popup().options.html).filter(node => node.href).map(node => node.href),
    ["https://app.test/flat/b", "https://app.test/flat/b#house"]);
  assert.equal(view.links[0].hidden, true);
  assert.match(view.status.textContent, /Домов на карте: 2 · квартир: 2/);
  view.cards[2].hidden = true;
  view.refresh();
  assert.equal(view.visibleMarkers().length, 1);
  assert.equal(view.popup(), undefined);
  view.cards[0].hidden = false;
  view.refresh();
  assert.equal(view.visibleMarkers()[0].options.html.textContent, "2 квартиры");
  assert.equal(view.links[0].hidden, false);
});

test("empty results and undo reuse the native map and destroy old markers", () => {
  const view = mapPage();
  view.cards.forEach(card => { card.hidden = true; });
  view.refresh();
  assert.equal(view.visibleMarkers().length, 0);
  assert.equal(view.container.hidden, true);
  assert.equal(view.reset.disabled, true);
  assert.equal(view.shell.dataset.mapState, "empty");
  view.cards[1].hidden = false;
  view.refresh();
  assert.equal(view.maps.length, 1);
  assert.equal(view.visibleMarkers().length, 1);
  assert.equal(view.container.hidden, false);
  assert.equal(view.reset.disabled, false);
  assert.deepEqual(Array.from(view.maps[0].bounds.southWest), [131.91, 43.1]);
  assert.equal(view.maps[0].fitOptions.maxZoom, 16);
  assert.equal(view.maps[0].fitOptions.animation.animate, false);
});

test("a page starting with every listing skipped can reveal markers after undo", () => {
  const view = mapPage({ hidden: ["a", "b", "c"] });
  assert.equal(view.visibleMarkers().length, 0);
  view.cards[0].hidden = false;
  view.refresh();
  assert.equal(view.visibleMarkers().length, 1);
  assert.equal(view.container.hidden, false);
  assert.equal(view.maps.length, 1);
});

test("focus opens the right popup and closing it returns keyboard focus to the marker", () => {
  const view = mapPage();
  view.document.fire("farposton:map-focus", { key: "b" });
  assert.deepEqual(Array.from(view.maps[0].center), [131.9, 43.1]);
  assert.equal(view.maps[0].zoom, 16);
  const popup = view.popup();
  const close = popup.options.html.children[0];
  assert.equal(close.focused, undefined);
  view.frame();
  assert.equal(close.focused, true);
  close.fire("click");
  assert.equal(popup.destroyed, true);
  assert.equal(view.visibleMarkers()[0].options.html.focused, true);
});

test("SDK style failures show address links and a later idle event restores the map state", () => {
  const view = mapPage();
  view.maps[0].fire("styleloaderror");
  assert.equal(view.shell.dataset.mapState, "error");
  assert.equal(view.fallback.hidden, false);
  view.cards[0].hidden = true;
  view.refresh();
  assert.equal(view.links[0].hidden, true);
  view.maps[0].fire("idle");
  assert.equal(view.shell.dataset.mapState, "ready");
  assert.equal(view.fallback.hidden, true);
});

test("a stalled SDK shows usable addresses instead of loading forever", () => {
  const view = mapPage();
  assert.equal(view.timers[0].delay, 15000);
  view.timers[0].callback();
  assert.equal(view.shell.dataset.mapState, "error");
  assert.equal(view.fallback.hidden, false);
});

for (const options of [{ sdk: false }, { mapKey: "   " }, { fail: true }]) {
  test(`unavailable native map preserves address filtering: ${JSON.stringify(options)}`, () => {
    const view = mapPage(options);
    assert.equal(view.maps.length, 0);
    assert.equal(view.shell.dataset.mapState, "error");
    assert.equal(view.container.hidden, true);
    assert.equal(view.fallback.hidden, false);
    view.cards[0].hidden = true;
    view.refresh();
    assert.equal(view.links[0].hidden, true);
    view.cards[0].hidden = false;
    view.refresh();
    assert.equal(view.links[0].hidden, false);
  });
}

test("invalid coordinates and unsafe links never become markers or popup navigation", () => {
  const address = '<img src=x onerror="alert(1)">';
  const view = mapPage({ mutateLinks(links) {
    links[0].dataset.address = address;
    links[0].dataset.houseUrl = "javascript:alert(1)";
    links[1].dataset.lat = "Infinity";
    links[2].href = "https://external.test/flat";
  } });
  assert.equal(view.visibleMarkers().length, 1);
  view.visibleMarkers()[0].options.html.fire("click");
  const content = view.popup().options.html;
  assert.equal(descendants(content).find(node => node.className === "map-popup-address").textContent, address);
  assert.deepEqual(descendants(content).filter(node => node.href).map(node => node.href), ["https://app.test/flat/a", "https://app.test/flat/a#house"]);
});


test("group popup preserves each offer and validates image URLs", () => {
  const view = mapPage({ mutateLinks(links) {
    Object.assign(links[0].dataset, { photo: "https://images.test/one.jpg", rooms: "1-комн.", area: "40 м²", floor: "3/9 эт." });
    Object.assign(links[1].dataset, { photo: "javascript:alert(1)", rooms: "Студия", address: "Другой адрес" });
  } });
  view.visibleMarkers()[0].options.html.fire("click");
  const nodes = descendants(view.popup().options.html);
  const cards = nodes.filter(node => node.className === "map-popup-card");
  assert.equal(cards.length, 2);
  assert.equal(descendants(cards[0]).find(node => node.className === "map-popup-price").textContent, "10 000 ₽ / месяц");
  assert.equal(descendants(cards[0]).find(node => node.className === "map-popup-details").textContent, "1-комн. · 40 м² · 3/9 эт.");
  assert.equal(descendants(cards[1]).find(node => node.className === "map-popup-address").textContent, "Другой адрес");
  const img = descendants(cards[0]).find(node => node.src);
  assert.equal(img.src, "https://images.test/one.jpg");
  img.fire("error");
  assert.equal(img.removed, true);
  assert.equal(descendants(cards[1]).some(node => node.src), false);
  assert.deepEqual(nodes.filter(node => node.href).map(node => node.href), ["https://app.test/flat/a", "https://app.test/flat/b", "https://app.test/house/7"]);
});


test("hover keeps preview while entering it and closes after leaving", () => {
  const view = mapPage();
  const pill = view.visibleMarkers()[0].options.html;
  pill.fire("pointerenter");
  const popup = view.popup();
  assert.ok(popup);
  pill.fire("pointerleave");
  const timer = view.timers.at(-1);
  assert.equal(timer.delay, 200);
  popup.options.html.fire("pointerenter");
  assert.equal(timer.cleared, true);
  popup.options.html.fire("pointerleave");
  view.timers.at(-1).callback();
  assert.equal(popup.destroyed, true);
});

test("popup positions inside a narrow compact map", () => {
  const view = mapPage();
  view.container.getBoundingClientRect = () => ({ left: 20, right: 300, top: 100, bottom: 380, width: 280 });
  const pill = view.visibleMarkers()[0].options.html;
  pill.getBoundingClientRect = () => ({ left: 26, width: 60, top: 125, bottom: 155 });
  view.visibleMarkers()[0].options.html.fire("click");
  const content = view.popup().options.html;
  view.frame();
  assert.equal(content.dataset.placement, "below");
  assert.equal(content.style.maxWidth, "264px");
  assert.equal(content.style.maxHeight, "264px");
  assert.equal(content.style.marginLeft, "104px");
});


test("compact popup uses visible map height and shifts inside it", () => {
  const view = mapPage();
  view.container.getBoundingClientRect = () => ({ left: 0, right: 280, top: 100, bottom: 380, width: 280 });
  const pill = view.visibleMarkers()[0].options.html;
  pill.getBoundingClientRect = () => ({ left: 110, width: 60, top: 225, bottom: 255 });
  pill.fire("click");
  const content = view.popup().options.html;
  content.getBoundingClientRect = () => ({ top: -40, bottom: 224, height: 264 });
  view.frame();
  assert.equal(content.style.maxHeight, "264px");
  assert.equal(content.style.marginTop, "148px");
});

test("click after hover moves focus into existing preview", () => {
  const view = mapPage();
  const pill = view.visibleMarkers()[0].options.html;
  pill.fire("pointerenter");
  const popup = view.popup();
  const close = popup.options.html.children[0];
  view.frame();
  assert.equal(close.focused, undefined);
  pill.fire("click");
  assert.equal(view.popup(), popup);
  view.frame();
  assert.equal(close.focused, true);
});


test("popup placement uses settled marker bounds and keeps its close control inside the map", () => {
  const markerBounds = { left: 1100, top: 450, width: 60, bottom: 482 };
  const mapBounds = { left: 844, top: 522, right: 1399, bottom: 1002, width: 555 };
  const popupBounds = { left: 990, top: 378, width: 300, height: 200 };
  const view = mapPage({ markerBounds, mapBounds, popupBounds, viewport: { width: 1440, height: 1000 } });
  view.document.fire("farposton:map-focus", { key: "a" });
  const content = view.popup().options.html;
  assert.equal(content.style.maxHeight, undefined);
  markerBounds.top = 746;
  markerBounds.bottom = 778;
  view.frame();
  assert.equal(content.dataset.placement, "above");
  assert.equal(content.style.maxHeight, "360px");
  assert.equal(content.style.maxWidth, "300px");
  assert.equal(content.style.marginTop, "152px");
  assert.equal(content.children[0].focused, true);
  markerBounds.top = 580;
  markerBounds.bottom = 612;
  view.maps[0].fire("idle");
  assert.equal(content.dataset.placement, "below");
  assert.equal(content.style.maxHeight, "360px");
});


test("activating an already focused preview enters it and Escape returns to the marker", () => {
  const view = mapPage();
  const pill = view.visibleMarkers()[0].options.html;
  pill.fire("focus");
  view.frame();
  const popup = view.popup(), close = popup.options.html.children[0];
  assert.equal(close.focused, undefined);
  pill.fire("click");
  view.frame();
  assert.equal(close.focused, true);
  popup.options.html.fire("keydown", undefined, { key: "Escape" });
  assert.equal(popup.destroyed, true);
  assert.equal(pill.focused, true);
  pill.fire("focus");
  const next = view.popup();
  let prevented = false;
  pill.fire("keydown", undefined, { key: "Escape", preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(next.destroyed, true);
});
