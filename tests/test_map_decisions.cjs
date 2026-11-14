const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../flats/static/map.js"), "utf8");

function element(dataset = {}) {
  const listeners = new Map();
  return {
    dataset, hidden: false, children: [], textContent: "",
    append(...children) { this.children.push(...children); },
    setAttribute(name, value) { this[name] = value; },
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
    fire(type, detail) { for (const callback of listeners.get(type) || []) callback({ type, detail }); },
  };
}

function mapPage({ hidden = [], leaflet = true } = {}) {
  const cards = ["a", "b", "c"].map(key => ({ dataset: { listingKey: key }, hidden: hidden.includes(key) }));
  const links = cards.map((card, index) => Object.assign(element({
    key: card.dataset.listingKey, lat: "43.1", lon: String(131.9 + index / 100),
    houseId: index < 2 ? "7" : "9", houseUrl: index < 2 ? "/house/7" : "/house/9",
    price: `${index + 1}0 000 ₽`, address: index < 2 ? "Дом 7" : "Дом 9",
  }), { href: `https://app.test/flat/${card.dataset.listingKey}` }));
  const container = Object.assign(element(), { clientWidth: 500, clientHeight: 300 });
  const fallback = element(), reset = element(), status = element(), shell = element();
  shell.querySelector = selector => ({
    "[data-map]": container, ".map-data": fallback,
    "[data-map-reset]": reset, "[data-map-status]": status,
  })[selector];
  shell.querySelectorAll = () => links;
  const document = Object.assign(element(), {
    querySelectorAll: selector => selector === ".map-shell" ? [shell] : cards,
    createElement: () => element(),
  });
  const maps = [], layers = [], tiles = [];
  const L = {
    map(_container, options) {
      const map = {
        ready: Boolean(options.center && options.zoom !== undefined),
        invalidateSize() {},
        fitBounds(bounds) { this.ready = true; this.bounds = bounds; },
        setView(center) { this.ready = true; this.center = center; },
      };
      maps.push(map);
      return map;
    },
    control: { zoom: () => ({ addTo() {} }) },
    layerGroup() {
      const layer = {
        items: [],
        addTo(map) { this.map = map; return this; },
        clearLayers() { this.items = []; },
      };
      layers.push(layer);
      return layer;
    },
    divIcon: options => options,
    marker(coords, options) {
      return {
        coords, options, node: element(),
        addTo(layer) { this.map = layer.map; layer.items.push(this); return this; },
        // Leaflet creates marker elements only after the map has a view.
        getElement() { return this.map.ready ? this.node : undefined; },
        bindPopup(popup) { this.popup = popup; },
        getLatLng() { return this.coords; },
        openPopup() { this.opened = true; },
      };
    },
    tileLayer() {
      const layer = Object.assign(element(), {
        on(type, callback) { this.addEventListener(type, callback); return this; },
        addTo() { return this; },
      });
      tiles.push(layer);
      return layer;
    },
  };
  vm.runInNewContext(source, {
    document, window: { L: leaflet ? L : undefined }, L, URL,
    location: { href: "https://app.test/", origin: "https://app.test" },
    matchMedia: () => ({ matches: true }),
  });
  return { cards, links, container, fallback, reset, status, shell, maps, layers, tiles,
    refresh() { document.fire("decisionschange"); } };
}

test("skipping and undoing a listing updates grouped markers, popup links and counts", () => {
  const view = mapPage();
  view.tiles[0].fire("load");
  assert.equal(view.layers[0].items.length, 2);
  assert.equal(view.layers[0].items[0].options.icon.html.textContent, "2 квартиры");
  assert.match(view.status.textContent, /Домов на карте: 2 · квартир: 3/);
  view.cards[0].hidden = true;
  view.refresh();
  const house = view.layers[0].items[0];
  assert.equal(house.options.icon.html.textContent, "20 000 ₽");
  assert.deepEqual(house.popup.children.filter(node => node.href).map(node => node.href), ["/house/7", "https://app.test/flat/b"]);
  assert.equal(view.links[0].hidden, true);
  assert.match(view.status.textContent, /Домов на карте: 2 · квартир: 2/);
  view.cards[2].hidden = true;
  view.refresh();
  assert.equal(view.layers[0].items.length, 1);
  view.cards[0].hidden = false;
  view.refresh();
  assert.equal(view.layers[0].items[0].options.icon.html.textContent, "2 квартиры");
  assert.equal(view.links[0].hidden, false);
});

test("empty results and undo reuse the map and marker layer", () => {
  const view = mapPage();
  view.cards.forEach(card => { card.hidden = true; });
  view.refresh();
  assert.equal(view.layers[0].items.length, 0);
  assert.equal(view.container.hidden, true);
  assert.equal(view.reset.disabled, true);
  assert.equal(view.shell.dataset.mapState, "empty");
  view.cards[1].hidden = false;
  view.refresh();
  assert.equal(view.maps.length, 1);
  assert.equal(view.layers.length, 1);
  assert.equal(view.layers[0].items.length, 1);
  assert.equal(view.container.hidden, false);
  assert.equal(view.reset.disabled, false);
  assert.equal(view.maps[0].bounds.length, 1);
});

test("a page starting with every listing skipped can reveal markers after undo", () => {
  const view = mapPage({ hidden: ["a", "b", "c"] });
  assert.equal(view.layers[0].items.length, 0);
  view.cards[0].hidden = false;
  view.refresh();
  assert.equal(view.layers[0].items.length, 1);
  assert.equal(view.container.hidden, false);
  assert.equal(view.maps.length, 1);
});

for (const leaflet of [true, false]) {
  test(`${leaflet ? "tile failure" : "missing Leaflet"} fallback hides skipped links and restores them on undo`, () => {
    const view = mapPage({ leaflet });
    if (leaflet) view.tiles[0].fire("tileerror");
    assert.equal(view.shell.dataset.mapState, "error");
    assert.equal(view.fallback.hidden, false);
    view.cards[0].hidden = true;
    view.refresh();
    assert.equal(view.links[0].hidden, true);
    assert.equal(view.links[1].hidden, false);
    assert.equal(view.fallback.hidden, false);
    view.cards[0].hidden = false;
    view.refresh();
    assert.equal(view.links[0].hidden, false);
  });
}
