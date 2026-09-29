// Run with node tests/test_dom_helper.cjs [revision-feed.json]. No dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');

// Minimal DOM surface used by the shared helper and revision cards.
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.style = {}; this.attrs = {}; }
  append(...items) { this.children.push(...items.map(x => x instanceof Element ? x : String(x))); }
  appendChild(item) { this.append(item); return item; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  set textContent(text) { this.children = [String(text)]; }
  get textContent() { return this.children.map(x => x instanceof Element ? x.textContent : x).join(''); }
}
const context = vm.createContext({
  document: {createElement: tag => new Element(tag), documentElement: {dataset: {}},
    readyState: 'loading', addEventListener() {}},
  localStorage: {getItem: () => 'light'},
});
vm.runInContext(fs.readFileSync(path.join(root, 'web/static/assets/app.js'), 'utf8'), context);
const el = context.el;
assert.equal(el('span', {}, 'Кухня').textContent, 'Кухня');
assert.equal(el('span', {}, 0).textContent, '0');
assert.equal(el('span').children.length, 0);
assert.equal(el('span', {}, null).children.length, 0);
const child = el('b', {text: 'bold'});
assert.equal(el('div', {}, child).children[0], child);
assert.equal(el('div', {}, ['before ', child, ' after']).textContent, 'before bold after');
const unsafeText = '<img src=x onerror=alert(1)>';
assert.equal(el('span', {}, unsafeText).children[0], unsafeText);

const page = path.join(root, 'web/static/revisions.html');
if (fs.existsSync(page)) {
  const html = fs.readFileSync(page, 'utf8');
  const script = html.slice(html.indexOf('const STATUS_CLASS'), html.indexOf('async function load()'));
  assert.ok(script.includes('function revCard'));
  vm.runInContext(script, context);
  const items = process.argv[2] ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).items : [{
    point_key: 'demo', point_name: 'Demo point', date: '2026-09-29',
    docs: [{area: 'Кухня', status: 'заполняется', number: 'DEMO-1', n_lines: 13,
      fact_share: 0.5, fact_total: 4, fact_filled: 2, decisions: 1}],
    runs: {dirty: {finished_at: '2026-09-29'}},
  }];
  assert.ok(items.length > 0);
  for (const item of items) {
    const card = context.revCard(item);
    assert.ok(card.textContent.includes(item.point_name));
    assert.ok(card.textContent.includes('Открыть'));
    assert.equal(card.children[1].children.length, item.docs.length);
  }
  console.log(`Rendered ${items.length} revision cards`);
}
console.log('DOM helper regression checks passed');
