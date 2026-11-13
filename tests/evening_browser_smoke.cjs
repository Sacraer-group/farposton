#!/usr/bin/env node
/* Real browser smoke for Evening MVP container. */
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
    // 1) load /evening
    await page.goto(`${BASE_URL}/evening`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#plan-form:not(.hidden)', { timeout: 15000 });
    await shot(page, '01-evening-loaded.png');
    const title = await page.textContent('h1');
    assert(title && title.includes('Готовый вечер'), `unexpected title: ${title}`);
    const bodyText = await page.textContent('body');
    assert(bodyText.includes('Вымышленные данные'), 'synthetic marking missing on /evening');

    // 2) choose real seed event (from live API)
    const eventsRes = await page.evaluate(async (base) => {
      const r = await fetch(`${base}/api/evening/events`, { credentials: 'same-origin' });
      return { ok: r.ok, status: r.status, data: await r.json().catch(()=>null) };
    }, BASE_URL);
    assert(eventsRes.ok, `events API failed: ${eventsRes.status}`);
    const items = (eventsRes.data && eventsRes.data.items) || [];
    console.log('events:', items.map(e => ({ id: e.id, title: e.title, date: e.date, starts_at: e.starts_at, status: e.status })));
    const scheduled = items.find(e => e.status === 'scheduled');
    assert(scheduled, 'no scheduled seed event found');
    await page.selectOption('#event', scheduled.id);
    // ensure date matches event
    await page.evaluate((d) => { document.getElementById('date').value = d; }, scheduled.date);

    // 3) valid window/traits
    // meal_end must be <= event start local time; pick safe window before event
    const startsAt = new Date(scheduled.starts_at);
    // compute local HH:MM in Asia/Vladivostok by formatting
    const fmt = (d) => new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Vladivostok', hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
    // use 18:00 start and 30 min before event end as safe values if possible, else fixed
    await page.fill('#meal_start', '18:00');
    const evHM = fmt(startsAt); // HH:MM
    let mealEnd = '18:30';
    const [eh, em] = evHM.split(':').map(Number);
    const endMins = eh * 60 + em - 15; // 15 min before event
    if (endMins > 18 * 60 + 1) {
      mealEnd = `${String(Math.floor(endMins/60)).padStart(2,'0')}:${String(endMins%60).padStart(2,'0')}`;
    }
    await page.fill('#meal_end', mealEnd);
    await page.fill('#max_distance_m', '3000');
    // pick a preferred trait (valid, no intersection)
    await page.check('input[name="preferred_features"][value="laptop"]');
    await shot(page, '02-form-filled.png');
    console.log('chosen event:', { id: scheduled.id, title: scheduled.title, date: scheduled.date, meal: `18:00-${mealEnd}`, ev: evHM });

    // 4) POST create from UI
    await Promise.all([
      page.waitForResponse(r => r.url().includes('/api/evening/plans') && r.request().method() === 'POST', { timeout: 20000 }),
      page.click('#submit-btn'),
    ]);
    await page.waitForSelector('#result:not(.hidden)', { timeout: 20000 });
    await shot(page, '03-result.png');

    // 5) primary/planB/explicit synthetic marking
    const resultText = await page.textContent('#result');
    assert(resultText.includes('Основной вариант'), 'primary option missing');
    assert(resultText.includes('План Б'), 'plan B missing');
    assert(resultText.includes('вымышлен') || resultText.includes('Вымышлен'), 'synthetic marking missing in result');
    assert(resultText.includes('fictional_seed') || resultText.includes('Вымышленн'), 'source/check marking missing');
    assert(resultText.includes('Подтверждённые признаки') || resultText.includes('Совпавшие предпочтения') || resultText.includes('Неизвестно'), 'feature explanation markers missing');
    const planLink = await page.getAttribute('#plan-link', 'href');
    assert(planLink && /\/evening\/p\//.test(planLink), `plan link missing or invalid: ${planLink}`);
    console.log('plan link:', planLink);

    // 6) open share link /evening/p/id and see plan
    const planPage = await context.newPage();
    await planPage.goto(planLink, { waitUntil: 'networkidle' });
    await planPage.waitForSelector('#plan:not(.hidden)', { timeout: 20000 });
    await shot(planPage, '04-plan-page.png');
    const planText = await planPage.textContent('#plan');
    assert(planText.includes('Основной вариант'), 'primary missing on plan page');
    assert(planText.includes('План Б'), 'plan B missing on plan page');
    assert(planText.includes(scheduled.title) || planText.includes('Событие'), 'event/plan info missing on plan page');
    assert(planText.includes('Текущий статус'), 'current status missing on plan page');
    assert(planText.includes('fictional_seed') || planText.includes('Источники'), 'sources/check date missing on plan page');
    const planStatus = await planPage.textContent('.status');
    console.log('plan page status:', planStatus);
    await planPage.close();

    // 7) invalid input shows error without crash
    await page.goto(`${BASE_URL}/evening`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#plan-form:not(.hidden)', { timeout: 15000 });
    await page.selectOption('#event', scheduled.id);
    await page.evaluate((d) => { document.getElementById('date').value = d; }, scheduled.date);
    await page.fill('#meal_start', '18:00');
    await page.fill('#meal_end', mealEnd);
    await page.fill('#max_distance_m', '3000');
    // create intersection: same feature required and preferred
    await page.check('input[name="required_features"][value="dog"]');
    await page.check('input[name="preferred_features"][value="dog"]');
    await page.click('#submit-btn');
    await page.waitForSelector('#form-error:not(.hidden)', { timeout: 10000 });
    const errText = await page.textContent('#form-error');
    assert(errText && errText.trim().length > 0, 'invalid input error text missing');
    console.log('invalid input error:', errText.trim());
    // page still usable/no crash
    assert(await page.isVisible('#plan-form'), 'form not visible after invalid input');
    assert(await page.isEnabled('#submit-btn'), 'submit not enabled after invalid input');
    await shot(page, '05-invalid-input.png');

    // clear invalid state for responsive checks: uncheck preferred dog
    await page.uncheck('input[name="preferred_features"][value="dog"]');
    await page.uncheck('input[name="required_features"][value="dog"]');

    // 8) widths 320 and 1280 without horizontal overflow
    await page.goto(`${BASE_URL}/evening`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#plan-form:not(.hidden)', { timeout: 15000 });
    await checkNoOverflow(page, 1280, 'evening');
    await checkNoOverflow(page, 320, 'evening');

    // also check plan page responsiveness
    const planPage2 = await context.newPage();
    await planPage2.goto(planLink, { waitUntil: 'networkidle' });
    await planPage2.waitForSelector('#plan:not(.hidden)', { timeout: 20000 });
    await checkNoOverflow(planPage2, 1280, 'plan');
    await checkNoOverflow(planPage2, 320, 'plan');
    await planPage2.close();

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
