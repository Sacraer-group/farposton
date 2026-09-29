/* Общие помощники страниц сервиса прогноза Прогноз заказов. Без фреймворков.
   Файл подключён синхронно в <head> всех страниц — theme-init ниже успевает
   поставить data-theme до первой отрисовки (нет мигания темы). */

/* ---- тема: светлая/тёмная -------------------------------------------------- */
(function () {
  var t = null;
  try { t = localStorage.getItem('app_theme'); } catch (e) {}
  if (t !== 'light' && t !== 'dark')
    t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
})();

function toggleTheme() {
  var t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('app_theme', t); } catch (e) {}
  document.dispatchEvent(new CustomEvent('app-theme', { detail: t }));
}

/** fetch + JSON; при 401 (протухла сессия) уводит на /login. */
async function fetchJSON(url, opts) {
  const r = await fetch(url, opts || {});
  if (r.status === 401) { window.location = '/login'; throw new Error('401'); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((d && d.message) || ('HTTP ' + r.status));
  return d;
}

/** Число с пробелами-разделителями: 12345 → «12 345». */
function fmt(n) { return (n ?? 0).toLocaleString('ru-RU'); }

/** «5 / 2.963 / 0.8 / 0» → «5 / 3 / 0,8 / 0» (дроби 1С пугают — точное в title). */
function fmtWeeks(s) {
  if (!s) return '—';
  return String(s).split('/').map(p => {
    const v = Number(p.trim().replace(',', '.'));
    return isFinite(v) ? v.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) : p.trim();
  }).join(' / ');
}

/** День недели к ISO-дате: «пятница» (для селекторов дат — считаем от дней недели). */
const WEEKDAYS_RU = ['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье'];
function weekdayRu(iso) {
  const d = new Date(iso + 'T12:00:00');
  return isNaN(d) ? '' : WEEKDAYS_RU[(d.getDay() + 6) % 7];
}

/** Create an element with text, a DOM node, or an array of children. */
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v);
  }
  if (children != null) node.append(...(Array.isArray(children) ? children : [children]));
  return node;
}

/** Открывает исходную таблицу в большом диалоге и возвращает её без потери
 * обработчиков, фильтров и текущих данных. */
function openExpandedTable(wrap) {
  const table = wrap && wrap.querySelector('table');
  if (!table || document.querySelector('dialog.table-dialog[open]')) return;
  const returnFocus = document.activeElement;
  const parent = wrap.parentNode;
  const placeholder = document.createComment('table returns here');
  parent.insertBefore(placeholder, wrap);

  const card = parent.closest ? parent.closest('.card') : null;
  const heading = card && card.querySelector('h2,h3,h4');
  const titleText = table.getAttribute('aria-label') ||
    (heading && heading.textContent.trim()) || 'Таблица';
  const dialog = document.createElement('dialog');
  dialog.className = 'chart-dialog table-dialog';
  dialog.setAttribute('aria-label', 'Развёрнутая таблица: ' + titleText);
  const shell = document.createElement('div'); shell.className = 'chart-dialog-shell';
  const head = document.createElement('div'); head.className = 'chart-dialog-head';
  const title = document.createElement('strong'); title.textContent = titleText;
  const close = document.createElement('button');
  close.type = 'button'; close.className = 'chart-tool'; close.textContent = 'Закрыть';
  close.addEventListener('click', () => dialog.close());
  head.append(title, close);
  const viewport = document.createElement('div');
  viewport.className = 'chart-dialog-viewport table-dialog-viewport';
  viewport.append(wrap); shell.append(head, viewport); dialog.append(shell);

  function restore() {
    if (placeholder.parentNode) placeholder.parentNode.insertBefore(wrap, placeholder);
    placeholder.remove(); dialog.remove();
    if (wrap._appTopScrollUpdate) wrap._appTopScrollUpdate();
    if (returnFocus && typeof returnFocus.focus === 'function') returnFocus.focus();
  }
  dialog.addEventListener('close', restore, { once: true });
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  document.body.append(dialog);
  try { dialog.showModal(); close.focus(); }
  catch (error) { restore(); throw error; }
}

/** Верхняя полоса горизонтальной прокрутки, синхронная с таблицей (фидбек 12.08:
    «ползунок только внизу — до него добираться»). Возвращает upd() — дёрнуть
    после каждого перерендера таблицы. */
function attachTopScroll(wrap) {
  if (!wrap || wrap._appTopScrollUpdate) return wrap && wrap._appTopScrollUpdate;
  const tools = document.createElement('div'); tools.className = 'tbl-tools';
  const expand = document.createElement('button');
  expand.type = 'button'; expand.className = 'chart-tool';
  expand.textContent = 'Развернуть таблицу';
  expand.setAttribute('aria-label', 'Открыть таблицу на весь экран');
  expand.addEventListener('click', () => openExpandedTable(wrap));
  tools.append(expand); wrap.parentNode.insertBefore(tools, wrap);
  const top = document.createElement('div'); top.className = 'tbl-scroll-top';
  top.setAttribute('aria-hidden', 'true');
  const ghost = document.createElement('div'); top.append(ghost);
  wrap.parentNode.insertBefore(top, wrap);
  top.addEventListener('scroll', () => { wrap.scrollLeft = top.scrollLeft; });
  wrap.addEventListener('scroll', () => {
    top.scrollLeft = wrap.scrollLeft;
    wrap.classList.toggle('scrolled', wrap.scrollLeft > 2);
  });
  const upd = () => {
    ghost.style.width = wrap.scrollWidth + 'px';
    top.style.display = wrap.scrollWidth > wrap.clientWidth + 1 ? 'block' : 'none';
  };
  new ResizeObserver(upd).observe(wrap); upd();
  wrap._appTopScrollUpdate = upd;
  return upd;
}

/**
 * Добавляет к SVG-графику локальный масштаб и отдельную область прокрутки.
 * Генератор графиков остаётся серверным; здесь меняется только представление.
 */
function openExpandedChart(svg) {
  if (!svg) return;
  const returnFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'chart-dialog';
  dialog.setAttribute('aria-label', svg.getAttribute('aria-label') || 'Развёрнутый график');
  const shell = document.createElement('div'); shell.className = 'chart-dialog-shell';
  const head = document.createElement('div'); head.className = 'chart-dialog-head';
  const title = document.createElement('strong');
  title.textContent = svg.getAttribute('aria-label') || 'График';
  const close = document.createElement('button');
  close.type = 'button'; close.className = 'chart-tool'; close.textContent = 'Закрыть';
  close.addEventListener('click', () => dialog.close());
  head.append(title, close);
  const viewport = document.createElement('div'); viewport.className = 'chart-dialog-viewport';
  const clone = svg.cloneNode(true);
  clone.style.width = '100%'; clone.style.minWidth = '760px'; clone.style.maxWidth = 'none';
  viewport.append(clone); shell.append(head, viewport); dialog.append(shell);
  dialog.addEventListener('close', () => {
    dialog.remove();
    if (returnFocus && typeof returnFocus.focus === 'function') returnFocus.focus();
  });
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  document.body.append(dialog); dialog.showModal(); close.focus();
}

function enhanceChart(container) {
  if (!container || container.querySelector(':scope > .chart-viewport > svg')) return;
  const svg = container.querySelector(':scope > svg');
  if (!svg) return;

  const viewBox = (svg.getAttribute('viewBox') || '').trim().split(/\s+/).map(Number);
  const naturalWidth = viewBox.length === 4 && isFinite(viewBox[2]) ? viewBox[2] : 760;
  let scale = 100;

  const viewport = document.createElement('div');
  viewport.className = 'chart-viewport';
  svg.parentNode.insertBefore(viewport, svg);
  viewport.append(svg);

  const tools = document.createElement('div');
  tools.className = 'chart-tools';
  tools.setAttribute('role', 'group');
  tools.setAttribute('aria-label', 'Масштаб графика');
  const scaleLabel = document.createElement('span');
  scaleLabel.className = 'chart-scale';
  scaleLabel.setAttribute('aria-live', 'polite');

  function button(text, label, action) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chart-tool'; b.textContent = text;
    b.setAttribute('aria-label', label); b.title = label; b.onclick = action;
    return b;
  }
  function apply(next) {
    scale = Math.max(50, Math.min(200, next));
    svg.style.width = '100%';
    svg.style.minWidth = Math.round(naturalWidth * scale / 100) + 'px';
    svg.style.maxWidth = 'none';
    scaleLabel.textContent = scale + '%';
  }
  function fit() {
    scale = 100;
    svg.style.width = '100%';
    svg.style.minWidth = '0';
    svg.style.maxWidth = '100%';
    scaleLabel.textContent = 'по ширине';
  }

  tools.append(
    button('−', 'Уменьшить график', () => apply(scale - 25)),
    scaleLabel,
    button('+', 'Увеличить график', () => apply(scale + 25)),
    button('Сброс', 'Вернуть исходный масштаб', () => apply(100)),
    button('Вписать', 'Показать график целиком по ширине', fit),
    button('Развернуть', 'Открыть крупный график', () => openExpandedChart(svg)),
  );
  container.prepend(tools);
  container.dataset.chartEnhanced = '1';
  apply(100);
}

/** Улучшает все ещё не обработанные SVG внутри указанного узла. */
function enhanceCharts(root) {
  const scope = root || document;
  scope.querySelectorAll('.chart, .svgbox, .chartpanel').forEach(enhanceChart);
}

/**
 * Network-wide analytics is intentionally visible only to administrators.
 * The server repeats the same rule on pages and APIs; this merely keeps the
 * manager navigation uncluttered and prevents a misleading dead-end link.
 */
async function applyAccessNavigation() {
  try {
    const r = await fetch('/api/meta');
    if (r.status === 401) return;
    const meta = await r.json();
    if (meta.demo_no_auth) document.getElementById('logoutBtn')?.remove();
    if (meta && meta.access && meta.access.analytics)
      document.documentElement.dataset.adminAccess = '1';
  } catch (e) {
    // Fail closed in presentation: admin links stay hidden, direct URLs remain
    // protected and available after the next successful metadata request.
  }
}

if (document.readyState === 'loading')
  document.addEventListener('DOMContentLoaded', applyAccessNavigation);
else
  applyAccessNavigation();

/** Выход: гасим сессию и на страницу входа. */
async function logout() {
  await fetch('/logout', { method: 'POST' }).catch(() => {});
  window.location = '/login';
}
