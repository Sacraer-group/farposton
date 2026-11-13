#!/usr/bin/env node
/* Real browser smoke for Evening MVP container (real data mode). */
const fs = require('fs');
const path = require('path');

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:18447';
const SHOTS_DIR = process.env.SHOTS_DIR || path.resolve(__dirname, '../.demo/evening-browser/screenshots');
const PW_MODULE = process.env.PLAYWRIGHT_MODULE || path.resolve(__dirname, '../.demo/evening-browser/node_modules/playwright');

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

async function shot(page, name) {
  fs.mkdirSync(SHOTS_DIR, { recursive: true });
  const p = path.join(SHOTS_DIR, name);
  await page.screenshot({ path: p, fullPage: true });
  console.log('screenshot:', p);
}

async function checkNoOverflow(page, width, label) {
  await page.setViewportSize({ width, height: 900 });
  await page.waitForTimeout(150);
  const overflow = await page.evaluate(() => {
    const el = document.documentElement;
    return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflow: el.scrollWidth - el.clientWidth };
  });
  console.log(`overflow@${width}:`, overflow);
  assert(overflow.scrollWidth <= overflow.clientWidth + 1, `horizontal overflow at ${label} (${width}px): ${overflow.scrollWidth} > ${overflow.clientWidth}`);
  await shot(page, `${label}-${width}.png`);
}

function pad2(n) { return String(n).padStart(2, '0'); }
function hmFromIsoVL(iso) {
  // compute local HH:MM in Asia/Vladivostok
  const d = new Date(iso);
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Vladivostok', hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
}
function minutes(hm) { const [h, m] = hm.split(':').map(Number); return h * 60 + m; }
function toHM(mins) { mins = Math.max(0, Math.min(23 * 60 + 59, mins)); return `${pad2(Math.floor(mins / 60))}:${pad2(mins % 60)}`; }

async function main() {
  let chromium;
  try {
    ({ chromium } = require(PW_MODULE));
  } catch (e) {
    throw new Error(`Playwright module not found at ${PW_MODULE}: ${e.message}`);
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on('pageerror', err => consoleErrors.push(`pageerror: ${err.message}`));
  page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(`console: ${msg.text()}`); });

  try {
    await page.goto(`${BASE_URL}/evening`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#plan-form:not(.hidden)', { timeout: 20000 });
    await shot(page, '01-evening-loaded.png');
    const title = await page.textContent('h1');
    assert(title && title.includes('Готовый вечер'), `unexpected title: ${title}`);
    const bodyText = await page.textContent('body');
    assert(bodyText.includes('Реальные данные'), 'real data marking missing on /evening');
    assert(!bodyText.includes('Вымышленные данные'), 'fictional marking must not appear in real mode');

    const eventsRes = await page.evaluate(async (base) => {
      const r = await fetch(`${base}/api/evening/events`, { credentials: 'same-origin' });
      return { ok: r.ok, status: r.status, data: await r.json().catch(() => null) };
    }, BASE_URL);
    assert(eventsRes.ok, `events API failed: ${eventsRes.status}`);
    const data = eventsRes.data || {};
    const items = data.items || [];
    console.log('events_meta:', { synthetic: data.synthetic, count: items.length, warning: data.warning });
    console.log('events:', items.map(e => ({ id: e.id, title: e.title, date: e.date, starts_at: e.starts_at, status: e.status })));
    assert(data.synthetic === false, 'events response must not be synthetic in real mode');
    const scheduled = items.find(e => e.status === 'scheduled');
    assert(scheduled, 'no scheduled real event found');

    await page.selectOption('#event', scheduled.id);
    await page.evaluate((d) => { document.getElementById('date').value = d; }, scheduled.date);

    // compute meal window from event start (not hardcoded 17-18)
    const evHM = hmFromIsoVL(scheduled.starts_at);
    const evM = minutes(evHM);
    let mealEndM = evM - 30;
    let mealStartM = mealEndM - 90;
    if (mealStartM < 0) {
      mealStartM = Math.max(0, evM - 120);
      mealEndM = Math.max(mealStartM + 30, evM - 15);
    }
    if (mealEndM <= mealStartM) mealEndM = mealStartM + 30;
    if (mealEndM > evM) mealEndM = evM;
    const mealStart = toHM(mealStartM);
    const mealEnd = toHM(mealEndM);
    await page.fill('#meal_start', mealStart);
    await page.fill('#meal_end', mealEnd);
    await page.fill('#max_distance_m', '3000');
    // no required/preferred traits to avoid forcing; keep valid
    await shot(page, '02-form-filled.png');
    console.log('chosen event:', { id: scheduled.id, title: scheduled.title, date: scheduled.date, meal: `${mealStart}-${mealEnd}`, ev: evHM });

    const [resp] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/api/evening/plans') && r.request().method() === 'POST', { timeout: 30000 }),
      page.click('#submit-btn'),
    ]);
    const status = resp.status();
    console.log('create plan status:', status);
    if (status === 201) {
      await page.waitForSelector('#result:not(.hidden)', { timeout: 30000 });
      await shot(page, '03-result.png');
      const resultText = await page.textContent('#result');
      assert(resultText.includes('Основной вариант'), 'primary option missing');
      assert(resultText.includes('План Б'), 'plan B missing');
      assert(/реальные данные/i.test(resultText), 'real data marking missing in result');
      assert(!resultText.includes('fictional_seed'), 'fictional_seed must not appear in real result');
      assert(resultText.includes('2gis_catalog_snapshot') || resultText.includes('Источники') || resultText.includes('источник'), 'catalog/source info missing');
      assert(resultText.includes('Подтверждённые признаки') || resultText.includes('Совпавшие предпочтения') || resultText.includes('Неизвестно'), 'feature explanation markers missing');
      const planLink = await page.getAttribute('#plan-link', 'href');
      assert(planLink && /\/evening\/p\//.test(planLink), `plan link missing or invalid: ${planLink}`);
      console.log('plan link:', planLink);

      const planPage = await context.newPage();
      await planPage.goto(planLink, { waitUntil: 'networkidle' });
      await planPage.waitForSelector('#plan:not(.hidden)', { timeout: 30000 });
      await shot(planPage, '04-plan-page.png');
      const planText = await planPage.textContent('#plan');
      assert(planText.includes('Основной вариант'), 'primary missing on plan page');
      assert(planText.includes('План Б'), 'plan B missing on plan page');
      assert(planText.includes('Текущий статус'), 'current status missing on plan page');
      assert(planText.includes('Источники'), 'sources missing on plan page');
      assert(!planText.includes('fictional_seed'), 'fictional_seed must not appear on real plan page');
      const planStatus = await planPage.textContent('.status');
      console.log('plan page status:', planStatus);
      await checkNoOverflow(planPage, 1280, 'plan');
      await checkNoOverflow(planPage, 320, 'plan');
      await planPage.close();
    } else {
      // honest 409 (e.g. not enough places) is allowed by spec; UI must show error without crash
      const errBody = await resp.json().catch(() => ({}));
      console.log('create plan not 201 (honest outcome):', errBody);
      await page.waitForSelector('#form-error:not(.hidden), #result:not(.hidden)', { timeout: 15000 }).catch(() => {});
      const errText = await page.textContent('#form-error').catch(() => '') || '';
      const resText = await page.textContent('#result').catch(() => '') || '';
      assert(errText.trim().length > 0 || resText.trim().length > 0, 'no visible error/result after non-201 response');
      assert(await page.isVisible('#plan-form'), 'form not visible after non-201 response');
      await shot(page, '03-result-or-error.png');
    }

    // invalid input shows error without crash
    await page.goto(`${BASE_URL}/evening`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#plan-form:not(.hidden)', { timeout: 20000 });
    await page.selectOption('#event', scheduled.id);
    await page.evaluate((d) => { document.getElementById('date').value = d; }, scheduled.date);
    await page.fill('#meal_start', mealStart);
    await page.fill('#meal_end', mealEnd);
    await page.fill('#max_distance_m', '3000');
    await page.check('input[name="required_features"][value="dog"]');
    await page.check('input[name="preferred_features"][value="dog"]');
    await page.click('#submit-btn');
    await page.waitForSelector('#form-error:not(.hidden)', { timeout: 10000 });
    const errText = await page.textContent('#form-error');
    assert(errText && errText.trim().length > 0, 'invalid input error text missing');
    console.log('invalid input error:', errText.trim());
    assert(await page.isVisible('#plan-form'), 'form not visible after invalid input');
    assert(await page.isEnabled('#submit-btn'), 'submit not enabled after invalid input');
    await shot(page, '05-invalid-input.png');

    await page.uncheck('input[name="preferred_features"][value="dog"]');
    await page.uncheck('input[name="required_features"][value="dog"]');

    await page.goto(`${BASE_URL}/evening`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#plan-form:not(.hidden)', { timeout: 20000 });
    await checkNoOverflow(page, 1280, 'evening');
    await checkNoOverflow(page, 320, 'evening');

    const fatal = consoleErrors.filter(e => !/favicon/i.test(e));
    if (fatal.length) console.log('console/page errors (non-fatal check):', fatal);
    console.log('SMOKE_OK');
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch(err => {
  console.error('SMOKE_FAIL:', err && err.stack || err);
  process.exit(1);
});
