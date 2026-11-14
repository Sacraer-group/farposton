const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element() {
  const events = {};
  return { hidden: true, complete: false, currentSrc: '', naturalWidth: 0, addEventListener(name, fn) { events[name] = fn; }, fire(name) { events[name](); } };
}
function gallery(photos, selected = 0, controls = true) {
  const image = element(), error = element(), prev = controls ? element() : null, next = controls ? element() : null, count = controls ? element() : null;
  image.hidden = false;
  const nodes = { '[data-gallery-image]': image, '[data-gallery-error]': error, '[data-gallery-prev]': prev, '[data-gallery-next]': next, '[data-gallery-count]': count };
  return { dataset: { photos: JSON.stringify(photos), index: String(selected) }, querySelector: key => nodes[key], image, error, prev, next, count };
}
const a = gallery(['one', 'two', 'three'], 1), b = gallery(['only'], 0, false);
vm.runInNewContext(fs.readFileSync('flats/static/gallery.js', 'utf8'), { document: { querySelectorAll: () => [a, b] } });
assert.equal(a.count.textContent, '2 / 3');
a.image.fire('error'); assert.equal(a.image.hidden, true); assert.equal(a.error.hidden, false);
a.next.fire('click'); assert.equal(a.image.src, 'three'); assert.equal(a.count.textContent, '3 / 3'); assert.equal(a.error.hidden, true);
a.next.fire('click'); assert.equal(a.image.src, 'one');
a.prev.fire('click'); assert.equal(a.image.src, 'three');
a.image.fire('error'); a.prev.fire('click'); a.image.fire('load'); assert.equal(a.image.hidden, false);
b.image.fire('error'); assert.equal(b.error.hidden, false);
console.log('gallery navigation and recovery: ok');

// Gallery buttons must never initiate a swipe or trigger the swipe hotkeys.
const listeners = {};
const stackListeners = {};
let writes = 0, captures = 0;
const topButton = {}, lowerButton = {};
const card = { dataset: { source: 'test', sourceId: '1' }, contains: () => true, setPointerCapture: () => captures++, querySelectorAll: () => [topButton], style: {} };
const lower = { querySelectorAll: () => [lowerButton] };
const stack = { querySelectorAll: () => [lower, card], addEventListener: (name, fn) => stackListeners[name] = fn };
const buttons = Object.fromEntries(['save', 'skip', 'details', 'undo'].map(id => [id, { addEventListener() {} }]));
const document = {
  getElementById: id => id === 'stack' ? stack : id === 'empty' ? {} : buttons[id],
  querySelectorAll: () => [],
  addEventListener: (name, fn) => listeners[name] = fn,
};
const decisions = { current: () => [], decide: () => writes++, undo() {}, reportError() {} };
vm.runInNewContext(fs.readFileSync('flats/static/swipe.js', 'utf8'), { document, window: { FlatsDecisions: decisions }, matchMedia: () => ({ matches: true }) });
assert.equal(lowerButton.disabled, true); assert.equal(topButton.disabled, false);
const target = { closest: selector => selector.includes('button') ? {} : null };
stackListeners.pointerdown({ target, clientX: 0, pointerId: 1 });
stackListeners.pointerup({ target, clientX: 150 });
listeners.keydown({ target, key: 'ArrowRight' });
assert.equal(captures, 0); assert.equal(writes, 0);
console.log('gallery controls do not trigger swipe actions: ok');
