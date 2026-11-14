const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag = '') { this.tag = tag; this.children = []; this.listeners = {}; this.hidden = false; this.value = ''; this.elements = {}; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  fire(type, extra = {}) { return this.listeners[type]?.({ preventDefault() {}, currentTarget: this, target: this, ...extra }); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {}
  querySelectorAll(selector) { return selector === 'input' ? [] : []; }
  querySelector() { return new Element(); }
  get lastElementChild() { return this.children.at(-1); }
}
const ids = ['trip-app', 'trip-status', 'trip-places', 'trip-legs', 'start-trips', 'partial-trips', 'trip-result-link', 'quick-schedule', 'custom-schedule-summary', 'quick-place', 'quick-out', 'quick-back', 'add-leg', 'place-search', 'place-results', 'place-query', 'manual-place', 'place-picker-status', 'cancel-trips', 'trip-progress'];
const els = Object.fromEntries(ids.map(id => [id, new Element()]));
els['trip-app'].querySelectorAll = () => ids.filter(id => !['trip-app', 'cancel-trips'].includes(id)).map(id => els[id]);
const details = new Element();
const document = { querySelector: s => s === '.manual-place' ? details : els[s.slice(1)], createElement: tag => new Element(tag), addEventListener(type, fn) { this[type] = fn; } };
let choices = [], storageError = false;
const window = { FlatsDecisions: { current: () => { if (storageError) throw new Error('invalid'); return choices; } }, addEventListener() {} };
let requests = [], budget = false, delayStart = false, releaseStart, delayCancel = false, releaseCancel;
let releaseLoad;
const load = new Promise(resolve => releaseLoad = resolve);
const fetch = async (url, options = {}) => {
  const body = options.body && JSON.parse(options.body); requests.push({ url, body });
  let data = {};
  if (url.endsWith('/schedule') && !body) { await load; data = { version: 0, schedule: { places: [], legs: [] } }; }
  else if (url.endsWith('/schedule')) data = { version: 1 };
  else if (url.includes('/places?')) data = { places: [{ id: 'p', name: 'Работа', lat: 43, lon: 132 }] };
  else if (url.endsWith('/estimate')) data = { candidates: 2, total_tasks: 20, unique_queries: 20, remaining_session: budget ? 1 : 30, remaining_global: 30, sufficient_budget: !budget };
  else if (url.endsWith('/start')) { if (delayStart) await new Promise(resolve => releaseStart = resolve); data = { id: 'job', status: 'complete', done: 20, total: 20 }; }
  else if (url.endsWith('/cancel') && delayCancel) await new Promise(resolve => releaseCancel = resolve);
  return { ok: true, json: async () => data };
};
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  vm.runInNewContext(fs.readFileSync('flats/static/trips.js', 'utf8'), { document, window, fetch, crypto: { randomUUID: (() => { let n = 0; return () => `id${++n}`; })() }, console });
  assert.equal(els['start-trips'].disabled, true);
  document.decisionschange();
  assert.equal(els['start-trips'].disabled, true, 'refresh cannot unlock initial load');
  releaseLoad(); await tick();
  assert.equal(els['start-trips'].disabled, false);
  els['place-query'].value = 'Работа';
  await els['place-search'].fire('submit');
  els['place-results'].children[0].children[2].fire('click');
  assert.equal(els['quick-out'].value, '08:00'); assert.equal(els['quick-back'].value, '18:00');
  const first = els['start-trips'].fire('click');
  els['start-trips'].fire('click');
  await first;
  assert.equal(requests.filter(r => r.url.endsWith('/start')).length, 1);
  const saved = requests.find(r => r.url.endsWith('/schedule') && r.body).body;
  assert.equal(saved.legs.length, 2); assert.deepEqual(Array.from(saved.legs[0].days), [0, 1, 2, 3, 4]);
  assert.equal(requests.find(r => r.url.endsWith('/start')).body.scope, 'all');
  budget = true;
  await els['start-trips'].fire('click');
  assert.equal(els['partial-trips'].hidden, false);
  assert.equal(requests.filter(r => r.url.endsWith('/start')).length, 1);
  await els['partial-trips'].fire('click');
  assert.equal(requests.filter(r => r.url.endsWith('/start')).length, 2);
  document.decisionschange();
  assert.equal(els['trip-result-link'].hidden, true);
  budget = false; storageError = true;
  await els['start-trips'].fire('click');
  assert.match(els['trip-status'].textContent, /Не удалось прочитать решения/);
  assert.equal(els['start-trips'].disabled, false, 'storage failure must unlock controls');
  storageError = false;
  delayStart = true; delayCancel = true;
  const pending = els['start-trips'].fire('click');
  while (!releaseStart) await tick();
  choices = [['metrapi', '1', 'skip']]; document.decisionschange();
  assert.equal(els['start-trips'].disabled, true, 'old start must settle before retry');
  const before = requests.filter(r => r.url.endsWith('/start')).length;
  await els['start-trips'].fire('click');
  assert.equal(requests.filter(r => r.url.endsWith('/start')).length, before);
  releaseStart();
  while (!releaseCancel) await tick();
  assert.equal(requests.at(-1).url, '/api/trips/job/cancel');
  assert.equal(els['start-trips'].disabled, true, 'retry waits for cancellation');
  releaseCancel(); await pending;
  assert.equal(els['start-trips'].disabled, false);
  assert.equal(els['trip-result-link'].hidden, true);
  delayStart = false; delayCancel = false;
  await els['start-trips'].fire('click');
  assert.equal(requests.filter(r => r.url.endsWith('/start')).length, before + 1);
  assert.equal(els['trip-result-link'].hidden, false, 'retry succeeds after cancellation');
  console.log('trips UI flow: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
