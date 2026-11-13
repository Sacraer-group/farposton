'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const DAY_NAMES = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'];
  const state = {category:'food', day:0, hour:12, source:'all', q:'', sort:'popular', open:false, offset:0, items:[], total:0, selected:null, selectedPlace:null, history:null, historyDate:null, historyHour:12, forecast:null, forecastDate:null, forecastHour:12, forecastRequest:0, forecastLoading:false, detailRequest:0, map:null, markers:null, request:0};
  let debounce;
  const hourSelect = $('hour');
  for (let i=0;i<24;i++) { const option = document.createElement('option'); option.value = String(i); option.textContent = `${String(i).padStart(2,'0')}:00`; hourSelect.append(option); }
  try {
    const parts = new Intl.DateTimeFormat('en-US', {timeZone:'Asia/Vladivostok',weekday:'short',hour:'numeric',hourCycle:'h23'}).formatToParts(new Date());
    state.day = Math.max(0, DAYS.indexOf(parts.find(x => x.type === 'weekday').value));
    state.hour = Number(parts.find(x => x.type === 'hour').value);
  } catch (_) { state.day = 0; state.hour = 12; }
  $('day').value = String(state.day); hourSelect.value = String(state.hour);

  function formatDate(value, long=false) {
    const date = new Date(`${value}T12:00:00Z`);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('ru-RU', {day:'numeric', month:long?'long':'short', timeZone:'UTC'}).format(date).replace(/\.$/, '');
  }

  function openingLabel(item) {
    if (item.opening_state === 'open') return ['Открыто по расписанию', ''];
    if (item.opening_state === 'closed') return ['Закрыто по расписанию', 'closed'];
    return ['Часы не указаны', 'unknown'];
  }
  function sourceLabel(item) {
    if (item.profile_type === '2gis') return 'Профиль 2ГИС · шкала не расшифрована';
    if (item.opening_state !== 'closed' && Number.isInteger(item.demo_index)) return `Демо · индекс ${item.demo_index}/100`;
    return 'Демо · искусственный профиль';
  }
  function rowMarkup(item) {
    const [opening, style] = openingLabel(item);
    const title = item.name.split(',')[0].trim() || item.name;
    const initial = (title.match(/[\p{L}\p{N}]/u) || ['✳'])[0].toUpperCase();
    const category = item.rubrics.slice(0,2).join(' · ') || 'Место в городе';
    const rating = item.rating == null ? '<div class="rating">—</div>' : `<div class="rating"><span>★</span> ${esc(item.rating.toFixed(1))}</div><div class="reviews">${esc(item.review_count.toLocaleString('ru-RU'))} отзывов</div>`;
    return `<article class="place-row"><div class="place-avatar" aria-hidden="true">${esc(initial)}</div><div class="place-main"><button class="place-name" type="button" data-place="${esc(item.id)}">${esc(title)}</button><div class="place-category">${esc(category)}</div><div class="place-address">${esc(item.address || 'Адрес не указан')}</div><div class="row-meta"><span class="state-tag ${style}">${opening}</span><span class="source-tag">${esc(sourceLabel(item))}</span></div></div><div class="row-right">${rating}<button class="row-arrow" type="button" data-place="${esc(item.id)}" aria-label="Открыть ${esc(title)}">↗</button></div></article>`;
  }
  async function fetchPlaces(append=false) {
    const request = ++state.request;
    if (!append) { state.offset=0; state.items=[]; $('results').innerHTML='<div class="loading">Ищем места во Владивостоке…</div>'; $('load-more').hidden=true; }
    const params = new URLSearchParams({category:state.category, source:state.source, q:state.q, sort:state.sort, day:String(state.day), hour:String(state.hour), open_only:String(state.open), offset:String(state.offset), limit:'48'});
    try {
      const response = await fetch('/api/places?' + params.toString());
      if (!response.ok) throw new Error(response.status === 401 ? 'Сессия завершилась. Войдите снова.' : 'Не удалось загрузить места. Попробуйте ещё раз.');
      const data = await response.json();
      if (request !== state.request) return;
      state.total = data.total; state.items = append ? state.items.concat(data.items) : data.items;
      $('result-count').textContent = data.total.toLocaleString('ru-RU');
      $('results').innerHTML = state.items.length ? state.items.map(rowMarkup).join('') : '<div class="empty">Ничего не нашлось.<br>Попробуйте изменить поиск или фильтры.</div>';
      $('shown-count').textContent = state.items.length ? `Показано ${state.items.length} из ${state.total}` : '';
      $('load-more').hidden = state.items.length >= state.total;
      $('map-count').textContent = `${state.items.length} НА КАРТЕ`;
      if (data.history?.synthetic) {
        $('history-overview').hidden = false;
        $('aside-days').innerHTML = `${esc(data.history.days)}<small>ДНЕЙ ДЕМО-ИСТОРИИ</small>`;
        $('history-period').textContent = `${formatDate(data.history.start_date)} — ${formatDate(data.history.end_date)} · по часам для каждой точки`;
      }
      drawMarkers();
    } catch (error) {
      if (request !== state.request) return;
      $('results').innerHTML = `<div class="empty">${esc(error.message)}<br><button class="load-more" id="retry" type="button">Повторить</button></div>`;
      $('result-count').textContent='—'; $('shown-count').textContent=''; $('load-more').hidden=true;
      $('retry')?.addEventListener('click', () => fetchPlaces());
    }
  }
  function initMap() {
    if (typeof window.L === 'undefined') { $('map-fallback').hidden=false; return; }
    try {
      state.map = L.map('map', {zoomControl:false, scrollWheelZoom:false}).setView([43.117,131.892],12);
      L.control.zoom({position:'bottomright'}).addTo(state.map);
      const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(state.map);
      let tileErrors = 0; tiles.on('tileerror', () => { if (++tileErrors >= 3) $('map-fallback').hidden=false; });
      state.markers = L.layerGroup().addTo(state.map);
    } catch (_) { $('map-fallback').hidden=false; }
  }
  function drawMarkers() {
    if (!state.markers) return;
    state.markers.clearLayers();
    for (const item of state.items) {
      if (item.lat == null || item.lon == null) continue;
      const dot = L.circleMarker([item.lat,item.lon], {radius:6, color:'#ffffff', weight:2, fillColor:'#315f49', fillOpacity:.95});
      dot.on('click', () => showDetail(item.id));
      const tooltip = document.createElement('span'); tooltip.textContent = item.name;
      dot.bindTooltip(tooltip, {direction:'top'}); dot.addTo(state.markers);
    }
  }
  function scheduleMarkup(schedule) {
    if (!schedule || typeof schedule !== 'object') return '<p>Часы работы не указаны. Для сгенерированного профиля условно принято 09:00–21:00.</p>';
    if (schedule.is_24x7 === true) return '<p>Круглосуточно по указанному расписанию.</p>';
    if (!DAYS.some(day => schedule[day])) return '<p>Часы работы не указаны.</p>';
    return `<div class="schedule-grid">${DAYS.map((day,index) => {
      const hours = schedule[day]?.working_hours;
      const text = Array.isArray(hours) && hours.length ? hours.map(x => `${esc(x.from)}–${esc(x.to)}`).join(', ') : 'Не указано';
      return `<div class="schedule-row"><span>${DAY_NAMES[index]}</span><strong>${text}</strong></div>`;
    }).join('')}</div><p>Обычное расписание. Праздники и изменения не учтены.</p>`;
  }
  function profileMarkup(place) {
    if (place.profile_type === '2gis') {
      const raw = place.congestion_2gis || {};
      const day = DAYS[state.day];
      const entries = Object.entries(raw).filter(([,days]) => Array.isArray(days?.[day]) && days[day].length).sort((a,b) => Number(a[0])-Number(b[0]));
      const text = entries.length ? entries.map(([level,days]) => `<div class="schedule-row"><span>Уровень ${esc(level)}</span><strong>${days[day].map(h => `${String(Number(h)).padStart(2,'0')}:00`).join(', ')}</strong></div>`).join('') : '<p>Для выбранного дня отметок нет.</p>';
      return `<section class="detail-card"><h3>Исходный профиль 2ГИС</h3><p>Отметки для дня «${DAY_NAMES[state.day]}». Смысл уровней и дата обновления неизвестны; это не данные о свободных местах.</p><div class="schedule-grid">${text}</div></section>`;
    }
    const profile = place.synthetic || {}; const values = profile.weekly_by_hour?.[DAYS[state.day]];
    if (!Array.isArray(values) || values.length !== 24) return '<section class="detail-card"><h3>Демо-профиль</h3><p>Для этого дня нет данных.</p></section>';
    const val = values[state.hour];
    const bars = values.map((v,i) => {
      const label = `${String(i).padStart(2,'0')}:00 — ${Number.isInteger(v) ? `демо-индекс ${v}` : 'нет демо-значения'}`;
      return `<button type="button" class="hour-bar-wrap" data-detail-hour="${i}" aria-label="${esc(label)}" aria-pressed="${i===state.hour}" title="${esc(label)}"><span class="hour-bar ${i===state.hour?'selected':''}" style="height:${Math.max(2, Math.min(100, Number(v)||0))}%"></span></button>`;
    }).join('');
    const assumption = profile.schedule_basis?.startsWith('assumed_09_21') ? ' Часы для этого профиля условно приняты 09:00–21:00.' : '';
    const basis = profile.generated_with === 'synthetic-history-weekly' ? 'Среднее по искусственной истории. ' : '';
    return `<section class="detail-card"><h3>Типовой день · ${DAY_NAMES[state.day]}</h3><p class="profile-notice">${basis}Искусственные данные для демонстрации. Не показывают посетителей или наличие свободных столов.${assumption}</p><div class="detail-value">${Number.isInteger(val)?esc(val):'—'} <small>/ 100 · демо-индекс в ${String(state.hour).padStart(2,'0')}:00</small></div><div class="detail-hours" role="group" aria-label="Сгенерированный профиль по часам для выбранного дня">${bars}</div><div class="hour-labels"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div></section>`;
  }
  function attributesMarkup(groups) {
    if (!Array.isArray(groups)) return '';
    const shown = groups.filter(x => x && x.name && Array.isArray(x.attributes) && x.attributes.length);
    if (!shown.length) return '';
    const render = group => `<div class="schedule-row"><span>${esc(group.name)}</span><strong>${esc(group.attributes.map(x => x.name || x.value || '').filter(Boolean).join(', '))}</strong></div>`;
    const rest = shown.length > 5 ? `<details class="more-attributes"><summary>Все характеристики (${shown.length})</summary><div class="schedule-grid">${shown.slice(5).map(render).join('')}</div></details>` : '';
    return `<section class="detail-card"><h3>О заведении</h3><div class="schedule-grid">${shown.slice(0,5).map(render).join('')}</div>${rest}</section>`;
  }
  function historyMarkup() {
    if (!state.history) return '<section class="history-panel"><div class="history-eyebrow">ИСТОРИЯ · ДЕМО</div><h3>Динамика за месяц</h3><p class="history-note">Загружаем демонстрационные данные…</p></section>';
    if (state.history.error) return '<section class="history-panel"><div class="history-eyebrow">ИСТОРИЯ · ДЕМО</div><h3>Динамика за месяц</h3><p class="history-note">История сейчас недоступна.</p><button type="button" class="history-retry" data-history-retry>Попробовать ещё раз ↗</button></section>';
    const history = state.history;
    const days = history.days || [];
    const selected = days.find(day => day.date === state.historyDate) || days.at(-1);
    if (!selected) return '<section class="history-panel"><h3>Демо-история пуста</h3></section>';
    const usable = days.filter(day => Number.isInteger(day.average));
    const overall = usable.length ? Math.round(usable.reduce((sum, day) => sum + day.average, 0) / usable.length) : null;
    const dayBars = days.map(day => {
      const value = Number.isInteger(day.average) ? day.average : null;
      const label = `${formatDate(day.date, true)} — ${value == null ? 'нет демо-значения' : `демо-индекс ${value}`}`;
      return `<button type="button" class="history-day-bar ${day.date===selected.date?'selected':''} ${value==null?'no-data':''}" data-history-date="${esc(day.date)}" aria-label="${esc(label)}" aria-pressed="${day.date===selected.date}" title="${esc(label)}"><span style="height:${value == null ? 3 : Math.max(3, Math.min(100, value))}%"></span></button>`;
    }).join('');
    const hourBars = selected.hourly.map((value, hour) => {
      const label = `${String(hour).padStart(2,'0')}:00 — ${value == null ? 'нет демо-значения' : `демо-индекс ${value}`}`;
      return `<button type="button" class="history-hour-bar ${hour===state.historyHour?'selected':''} ${value==null?'no-data':''}" data-history-hour="${hour}" aria-label="${esc(label)}" aria-pressed="${hour===state.historyHour}" title="${esc(label)}"><span style="height:${value == null ? 3 : Math.max(3, Math.min(100, value))}%"></span></button>`;
    }).join('');
    const selectedValue = selected.hourly[state.historyHour];
    const hourLabel = `${String(state.historyHour).padStart(2,'0')}:00`;
    const dateOptions = days.map(day => `<option value="${esc(day.date)}" ${day.date===selected.date?'selected':''}>${esc(formatDate(day.date, true))}</option>`).join('');
    const hourOptions = selected.hourly.map((value, hour) => `<option value="${hour}" ${hour===state.historyHour?'selected':''}>${String(hour).padStart(2,'0')}:00${value == null ? ' · нет значения' : ''}</option>`).join('');
    return `<section class="history-panel" aria-label="Сгенерированная история за месяц"><div class="history-heading"><div><div class="history-eyebrow"><span></span> ИСТОРИЯ · ИСКУССТВЕННЫЕ ДАННЫЕ</div><h3>Месяц в деталях<span>.</span></h3><p class="history-note">${esc(formatDate(history.start_date))} — ${esc(formatDate(history.end_date))} · Владивосток</p></div><div class="history-total"><strong>${overall == null ? '—' : esc(overall)}</strong><span>средний<br>демо-индекс</span></div></div><p class="history-disclaimer">Сгенерировано для демонстрации. Это не история посетителей и не наличие свободных мест.</p><div class="chart-heading"><span>ПО ДНЯМ</span><span>СРЕДНИЙ ИНДЕКС</span></div><div class="history-days" role="group" aria-label="Демо-индекс по дням">${dayBars}</div><div class="history-axis"><span>${esc(formatDate(history.start_date))}</span><span>Выберите день</span><span>${esc(formatDate(history.end_date))}</span></div><div class="history-hour-section"><div class="history-hour-heading"><div><span>ВЫБРАННЫЙ ДЕНЬ</span><label class="history-select"><select data-history-select aria-label="Дата демо-истории">${dateOptions}</select></label></div><div class="history-hour-value"><strong>${selectedValue == null ? '—' : esc(selectedValue)}</strong><span>/ 100 · ${hourLabel}</span></div></div><div class="history-hours" role="group" aria-label="Демо-индекс по часам выбранного дня">${hourBars}</div><div class="history-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div><label class="history-hour-select">Час <select data-history-hour-select aria-label="Час демо-истории">${hourOptions}</select></label></div><div class="history-caption">${history.schedule_basis?.startsWith('assumed_09_21') ? 'Часы для демо условно приняты 09:00–21:00. ' : ''}Значение — условный индекс; пустые часы не являются измерением.</div></section>`;
  }
  function forecastMarkup() {
    if (!state.forecast) return '<section class="forecast-panel" aria-live="polite"><h3>Демопрогноз на день</h3><p>Рассчитываем по искусственной истории…</p></section>';
    if (state.forecast.error) return '<section class="forecast-panel" aria-live="polite"><h3>Демопрогноз на день</h3><p>Прогноз сейчас недоступен.</p><button type="button" class="history-retry" data-forecast-retry>Попробовать ещё раз ↗</button></section>';
    const forecast = state.forecast;
    const selected = forecast.days.find(day => day.date === state.forecastDate) || forecast.days[0];
    const value = selected.hourly[state.forecastHour];
    const dateOptions = forecast.days.map(day => `<option value="${esc(day.date)}" ${day.date===selected.date?'selected':''}>${esc(new Intl.DateTimeFormat('ru-RU', {weekday:'short', day:'numeric', month:'short', timeZone:'UTC'}).format(new Date(`${day.date}T12:00:00Z`)))}</option>`).join('');
    const bars = selected.hourly.map((v, hour) => {
      const label = `${String(hour).padStart(2,'0')}:00 — ${v == null ? 'нет демопрогноза' : `демопрогноз ${v} из 100`}`;
      return `<button type="button" class="history-hour-bar ${hour===state.forecastHour?'selected':''} ${v==null?'no-data':''}" data-forecast-hour="${hour}" aria-label="${esc(label)}" aria-pressed="${hour===state.forecastHour}" title="${esc(label)}"><span style="height:${v == null ? 3 : Math.max(3, Math.min(100, v))}%"></span></button>`;
    }).join('');
    return `<section class="forecast-panel" aria-label="Демопрогноз модели"><div class="history-eyebrow"><span></span> АНСАМБЛЬ · ДЕМО</div><div class="forecast-heading"><div><h3>Прогноз на 7 дней<span>.</span></h3><label class="forecast-date">День <select data-forecast-date aria-label="День демопрогноза">${dateOptions}</select></label><p>Время Владивостока · обновляется каждый день</p></div><div class="history-hour-value"><strong>${value == null ? '—' : esc(value)}</strong><span>/ 100 · ${String(state.forecastHour).padStart(2,'0')}:00</span></div></div><p class="forecast-notice">Искусственная история до ${esc(formatDate(forecast.history_through, true))}; следующие дни рассчитаны по предыдущим прогнозам. Это не реальные наблюдения и не наличие свободных столов.</p><div class="history-hours" role="group" aria-label="Демопрогноз по часам">${bars}</div><div class="history-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div><p class="forecast-caption">Выберите день и час. Пустой столбец означает, что для него нет демопрогноза.</p></section>`;
  }
  function detailMarkup(place) {
    const title = place.name.split(',')[0].trim() || place.name;
    const source = place.profile_type === '2gis' ? '<span class="detail-badge actual">Профиль 2ГИС · шкала неизвестна</span>' : '<span class="detail-badge demo">Демо · профиль сгенерирован</span>';
    const rating = place.rating == null ? '' : `<span class="detail-badge">★ ${esc(place.rating.toFixed(1))} · ${esc(place.review_count.toLocaleString('ru-RU'))} отзывов</span>`;
    const days = DAY_NAMES.map((name,i) => `<button type="button" data-detail-day="${i}" aria-pressed="${i===state.day}" class="detail-day ${i===state.day?'active':''}">${name.slice(0,2)}</button>`).join('');
    return `<div class="detail-head"><h2 id="detail-title">${esc(title)}</h2><p class="detail-address">${esc(place.full_address || place.address || 'Адрес не указан')}</p></div><div class="detail-badges">${source}${rating}${place.rubrics.slice(0,2).map(x=>`<span class="detail-badge">${esc(x)}</span>`).join('')}</div>${forecastMarkup()}${historyMarkup()}<div class="detail-days" aria-label="День профиля">${days}</div>${profileMarkup(place)}<section class="detail-card"><h3>Часы работы</h3>${scheduleMarkup(place.schedule)}</section>${attributesMarkup(place.attribute_groups)}<a class="detail-link" href="https://2gis.ru/vladivostok/firm/${encodeURIComponent(place.id)}" target="_blank" rel="noopener noreferrer">Открыть в 2ГИС <span aria-hidden="true">↗</span></a>`;
  }
  async function showDetail(id) {
    state.selected = id;
    const request = ++state.detailRequest;
    state.selectedPlace = null; state.history = null; state.historyDate = null; state.historyHour = state.hour; state.forecast = null; state.forecastDate = null; state.forecastHour = state.hour; state.forecastLoading = false;
    $('detail-content').innerHTML='<div class="loading">Загружаем карточку…</div>';
    if (!$('detail-dialog').open) $('detail-dialog').showModal();
    try {
      const response = await fetch('/api/places/' + encodeURIComponent(id));
      if (!response.ok) throw new Error('Не удалось загрузить карточку.');
      const place = await response.json();
      if (state.selected !== id || request !== state.detailRequest) return;
      state.selectedPlace = place;
      $('detail-content').innerHTML = detailMarkup(place);
      loadHistory(id, request);
      loadForecast(id, request);
    } catch (error) {
      if (request === state.detailRequest) $('detail-content').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
  }
  async function loadForecast(id, request) {
    const forecastRequest = ++state.forecastRequest;
    state.forecastLoading = true;
    try {
      const response = await fetch('/api/places/' + encodeURIComponent(id) + '/forecast');
      if (!response.ok) throw new Error('Демопрогноз недоступен');
      const forecast = await response.json();
      if (state.selected !== id || request !== state.detailRequest || forecastRequest !== state.forecastRequest || !state.selectedPlace) return;
      if (forecast.synthetic !== true || !Array.isArray(forecast.days) || forecast.days.length !== 7 || forecast.days.some(day => !Array.isArray(day.hourly) || day.hourly.length !== 24)) throw new Error('Неверный формат демопрогноза');
      state.forecast = forecast;
      if (!forecast.days.some(day => day.date === state.forecastDate)) state.forecastDate = forecast.days[0].date;
    } catch (_) {
      if (state.selected !== id || request !== state.detailRequest || forecastRequest !== state.forecastRequest || !state.selectedPlace) return;
      state.forecast = {error:true};
    }
    state.forecastLoading = false;
    const scroll = $('detail-dialog').scrollTop;
    $('detail-content').innerHTML = detailMarkup(state.selectedPlace);
    $('detail-dialog').scrollTop = scroll;
  }
  async function loadHistory(id, request) {
    try {
      const response = await fetch('/api/places/' + encodeURIComponent(id) + '/history');
      if (!response.ok) throw new Error('История недоступна');
      const history = await response.json();
      if (state.selected !== id || request !== state.detailRequest || !state.selectedPlace) return;
      state.history = history;
      state.historyDate = history.days?.at(-1)?.date || null;
    } catch (_) {
      if (state.selected !== id || request !== state.detailRequest || !state.selectedPlace) return;
      state.history = {error:true};
    }
    const scroll = $('detail-dialog').scrollTop;
    $('detail-content').innerHTML = detailMarkup(state.selectedPlace);
    $('detail-dialog').scrollTop = scroll;
  }
  $('results').addEventListener('click', (event) => { const button=event.target.closest('[data-place]'); if (button) showDetail(button.dataset.place); });
  $('categories').addEventListener('click', (event) => { const button=event.target.closest('[data-category]'); if (!button) return; state.category=button.dataset.category; for (const chip of $('categories').querySelectorAll('.chip')) {const active=chip===button;chip.classList.toggle('active',active);chip.setAttribute('aria-pressed',String(active));} fetchPlaces(); });
  $('search').addEventListener('input', (event) => { clearTimeout(debounce); debounce=setTimeout(() => {state.q=event.target.value;fetchPlaces();},250); });
  document.addEventListener('keydown', (event) => {if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase()==='k') {event.preventDefault();$('search').focus();}});
  $('day').addEventListener('change', (event) => {state.day=Number(event.target.value);fetchPlaces();if ($('detail-dialog').open && state.selected) showDetail(state.selected);});
  $('hour').addEventListener('change', (event) => {state.hour=Number(event.target.value);fetchPlaces();if ($('detail-dialog').open && state.selected) showDetail(state.selected);});
  $('sort').addEventListener('change', (event) => {
    state.sort=event.target.value;
    if (state.sort.startsWith('demo_')) {state.source='synthetic';$('source').value='synthetic';}
    fetchPlaces();
  });
  $('source').addEventListener('change', (event) => {
    state.source=event.target.value;
    if (state.source !== 'synthetic' && state.sort.startsWith('demo_')) {state.sort='popular';$('sort').value='popular';}
    fetchPlaces();
  });
  $('open-only').addEventListener('click', (event) => {state.open=!state.open;event.currentTarget.setAttribute('aria-pressed',String(state.open));fetchPlaces();});
  $('load-more').addEventListener('click', () => {state.offset=state.items.length;fetchPlaces(true);});
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {document.querySelectorAll('[data-view]').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});$('results-layout').classList.toggle('show-map',button.dataset.view==='map');if(state.map)setTimeout(()=>state.map.invalidateSize(),50);}));
  $('detail-close').addEventListener('click', () => $('detail-dialog').close());
  $('detail-dialog').addEventListener('close', () => {state.selected=null;state.selectedPlace=null;state.history=null;state.forecast=null;state.detailRequest++;});
  $('detail-content').addEventListener('click', (event) => {
    const day = event.target.closest('[data-detail-day]');
    const hour = event.target.closest('[data-detail-hour]');
    const historyDate = event.target.closest('[data-history-date]');
    const historyHour = event.target.closest('[data-history-hour]');
    const historyRetry = event.target.closest('[data-history-retry]');
    const forecastHour = event.target.closest('[data-forecast-hour]');
    const forecastRetry = event.target.closest('[data-forecast-retry]');
    if (historyRetry && state.selected) { state.history=null; $('detail-content').innerHTML=detailMarkup(state.selectedPlace); loadHistory(state.selected, state.detailRequest); return; }
    if (forecastRetry && state.selected) { state.forecast=null; $('detail-content').innerHTML=detailMarkup(state.selectedPlace); loadForecast(state.selected, state.detailRequest); return; }
    if ((!day && !hour && !historyDate && !historyHour && !forecastHour) || !state.selectedPlace) return;
    if (day) {state.day=Number(day.dataset.detailDay);$('day').value=String(state.day);}
    if (hour) {state.hour=Number(hour.dataset.detailHour);$('hour').value=String(state.hour);}
    if (historyDate) state.historyDate=historyDate.dataset.historyDate;
    if (historyHour) state.historyHour=Number(historyHour.dataset.historyHour);
    if (forecastHour) state.forecastHour=Number(forecastHour.dataset.forecastHour);
    const scroll = $('detail-dialog').scrollTop;
    $('detail-content').innerHTML=detailMarkup(state.selectedPlace);
    $('detail-dialog').scrollTop = scroll;
    const focus = day ? `[data-detail-day="${state.day}"]` : hour ? `[data-detail-hour="${state.hour}"]` : historyDate ? `[data-history-date="${state.historyDate}"]` : historyHour ? `[data-history-hour="${state.historyHour}"]` : `[data-forecast-hour="${state.forecastHour}"]`;
    $('detail-content').querySelector(focus)?.focus({preventScroll:true});
    if (day || hour) fetchPlaces();
  });
  $('detail-content').addEventListener('change', (event) => {
    const isDate = event.target.matches('[data-history-select]');
    const isHour = event.target.matches('[data-history-hour-select]');
    const isForecastDate = event.target.matches('[data-forecast-date]');
    if ((!isDate && !isHour && !isForecastDate) || !state.selectedPlace) return;
    if (isDate) state.historyDate = event.target.value;
    if (isHour) state.historyHour = Number(event.target.value);
    if (isForecastDate) state.forecastDate = event.target.value;
    const scroll = $('detail-dialog').scrollTop;
    $('detail-content').innerHTML = detailMarkup(state.selectedPlace);
    $('detail-dialog').scrollTop = scroll;
    $('detail-content').querySelector(isDate ? '[data-history-select]' : isHour ? '[data-history-hour-select]' : '[data-forecast-date]')?.focus({preventScroll:true});
  });
  function refreshForecastOnNewDay() {
    if (!state.selected || !state.selectedPlace || state.forecastLoading) return;
    const parts = new Intl.DateTimeFormat('en-US', {timeZone:'Asia/Vladivostok',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    const part = type => parts.find(x => x.type === type)?.value;
    const today = `${part('year')}-${part('month')}-${part('day')}`;
    if (!state.forecast?.date || today !== state.forecast.date) loadForecast(state.selected, state.detailRequest);
  }
  setInterval(refreshForecastOnNewDay, 60000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) refreshForecastOnNewDay();});
  $('about-button').addEventListener('click', () => $('about-dialog').showModal());
  $('banner-info').addEventListener('click', () => $('about-dialog').showModal());
  $('history-about').addEventListener('click', () => $('about-dialog').showModal());
  $('pitch-preset').addEventListener('click', () => {
    state.category='food';state.source='synthetic';state.sort='demo_asc';state.day=4;state.hour=19;state.open=true;state.q='';
    $('source').value='synthetic';$('sort').value='demo_asc';$('day').value='4';$('hour').value='19';$('search').value='';
    $('open-only').setAttribute('aria-pressed','true');
    for (const chip of $('categories').querySelectorAll('.chip')) {
      const active=chip.dataset.category==='food';chip.classList.toggle('active',active);chip.setAttribute('aria-pressed',String(active));
    }
    $('results-layout').classList.remove('show-map');
    document.querySelectorAll('[data-view]').forEach(button=>{const active=button.dataset.view==='list';button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    fetchPlaces();
    $('results-layout').scrollIntoView({behavior:'smooth',block:'start'});
  });
  $('about-close').addEventListener('click', () => $('about-dialog').close());
  initMap(); fetchPlaces();
})();
