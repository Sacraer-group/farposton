(() => {
  const root = document.querySelector('#trip-app');
  if (!root) return;
  const status = document.querySelector('#trip-status');
  const placesList = document.querySelector('#trip-places');
  const legsList = document.querySelector('#trip-legs');
  const names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  let schedule = { places: [], legs: [] }, version = null, job = null, running = false, dirty = false, estimate = null, revision = 0, cancelRevision = null, cancellation = null;
  const uid = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text) n.textContent = text; if (cls) n.className = cls; return n; };
  const say = (text, error = false) => { status.textContent = text; status.className = error ? 'notice notice-error' : 'trip-status'; };
  async function api(path, options = {}) {
    const response = await fetch(`/api/trips${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || body.message || `Не удалось выполнить запрос (${response.status})`);
    return body;
  }
  function changed() {
    dirty = true;
    revision++;
    estimate = null;
    document.querySelector('#partial-trips').hidden = true;
    document.querySelector('#trip-result-link').hidden = true;
    syncQuick();
    say('График изменён. Подберите квартиры заново.');
  }
  function syncQuick() {
    const [out, back] = schedule.legs;
    const place = schedule.places.find(p => p.id === out?.to);
    const weekdays = leg => JSON.stringify(leg.days) === '[0,1,2,3,4]' && leg.max_minutes == null && leg.max_transfers == null;
    const simple = schedule.legs.length === 2 && place && out.from === 'home' && back.from === place.id && back.to === 'home' && weekdays(out) && weekdays(back);
    document.querySelector('#quick-schedule').hidden = !simple;
    const summary = document.querySelector('#custom-schedule-summary');
    summary.hidden = !!simple || !schedule.legs.length;
    summary.textContent = `В графике — мест: ${schedule.places.length}, участков: ${schedule.legs.length}. Изменить их можно ниже.`;
    if (simple) {
      document.querySelector('#quick-place').textContent = `Дом → ${place.name} → дом`;
      document.querySelector('#quick-out').value = out.departure;
      document.querySelector('#quick-back').value = back.departure;
    }
  }
  function button(text, action, cls = 'button button-small button-outline') {
    const b = node('button', text, cls); b.type = 'button'; b.disabled = running; b.addEventListener('click', action); return b;
  }
  function field(label, input) { const el = node('label', label); el.append(input); return el; }
  function select(value, onChange) {
    const s = node('select');
    for (const p of [{ id: 'home', name: 'Дом — из квартиры' }, ...schedule.places]) { const o = node('option', p.name); o.value = p.id; s.append(o); }
    s.value = value; s.addEventListener('change', () => { onChange(s.value); changed(); }); return s;
  }
  function render() {
    placesList.replaceChildren(); legsList.replaceChildren();
    schedule.places.forEach(p => { const li = node('li'); const detail = node('div'); detail.append(node('strong', p.name), node('small', `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)} · точка подтверждена`)); li.append(detail, button('Удалить', () => { if (schedule.legs.some(l => l.from === p.id || l.to === p.id)) { say('Сначала измените поездки с этим местом.', true); return; } schedule.places = schedule.places.filter(a => a.id !== p.id); changed(); render(); })); placesList.append(li); });
    schedule.legs.forEach((leg, index) => {
      const box = node('fieldset', null, 'trip-leg'); const legend = node('legend', `Поездка ${index + 1}`); box.append(legend);
      const controls = node('div', null, 'trip-leg-order');
      const move = delta => { const i = index + delta; if (i < 0 || i >= schedule.legs.length) return; [schedule.legs[i], schedule.legs[index]] = [schedule.legs[index], schedule.legs[i]]; changed(); render(); };
      controls.append(button('↑', () => move(-1)), button('↓', () => move(1)), button('Удалить', () => { schedule.legs.splice(index, 1); changed(); render(); })); controls.children[0].setAttribute('aria-label', `Поднять поездку ${index + 1}`); controls.children[1].setAttribute('aria-label', `Опустить поездку ${index + 1}`); box.append(controls);
      const endpoints = node('div', null, 'trip-two'); endpoints.append(field('Откуда', select(leg.from, v => leg.from = v)), field('Куда', select(leg.to, v => leg.to = v))); box.append(endpoints);
      const days = node('div', null, 'trip-days'); days.setAttribute('role', 'group'); days.setAttribute('aria-label', 'Дни недели');
      names.forEach((name, day) => { const c = node('input'); c.type = 'checkbox'; c.checked = leg.days.includes(day); c.addEventListener('change', () => { leg.days = c.checked ? [...leg.days, day].sort() : leg.days.filter(d => d !== day); changed(); }); const label = node('label'); label.append(c, node('span', name)); days.append(label); }); box.append(days);
      const limits = node('div', null, 'trip-limits');
      for (const [key, label, type, max] of [['departure', 'Выезд', 'time', null], ['max_minutes', 'Не дольше, мин', 'number', 1440], ['max_transfers', 'Пересадок не больше', 'number', 20]]) {
        const input = node('input'); input.type = type; input.value = leg[key] ?? ''; if (type === 'number') { input.min = key === 'max_minutes' ? '1' : '0'; input.max = max; input.placeholder = 'Не ограничено'; } else input.required = true;
        input.addEventListener('change', () => { leg[key] = type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value; changed(); }); limits.append(field(label, input));
      }
      box.append(limits); legsList.append(box);
    });
    document.querySelector('#add-leg').disabled = !schedule.places.length || running;
    syncQuick();
  }
  function addPlace(p) {
    if (schedule.places.some(a => a.id === String(p.id))) { say('Это место уже добавлено.', true); return; }
    const place = { id: String(p.id || uid()), name: p.name, lat: Number(p.lat), lon: Number(p.lon), confirmed: true };
    const empty = !schedule.places.length && !schedule.legs.length;
    schedule.places.push(place);
    if (empty) schedule.legs.push(...[['home', place.id, '08:00'], [place.id, 'home', '18:00']].map(([from, to, departure]) => ({ id: uid(), from, to, departure, days: [0,1,2,3,4], max_minutes: null, max_transfers: null })));
    changed(); render();
    document.querySelector('#place-results').replaceChildren();
    return true;
  }
  document.querySelector('#place-search').addEventListener('submit', async event => {
    event.preventDefault(); const results = document.querySelector('#place-results'); results.replaceChildren(node('p', 'Ищем места…'));
    try {
      const data = await api(`/places?q=${encodeURIComponent(document.querySelector('#place-query').value)}`); results.replaceChildren();
      for (const p of data.places || []) { const row = node('div', null, 'trip-place-result'); row.append(node('strong', p.name), node('small', p.address || `${p.lat}, ${p.lon}`), button('Это нужное место — добавить', () => addPlace(p))); results.append(row); }
      if (!results.children.length) results.append(node('p', 'Ничего не найдено. Уточните корпус или адрес; можно указать точку по координатам.'));
    } catch (error) { results.replaceChildren(node('p', error.message, 'notice notice-error')); }
  });
  const manual = document.querySelector('#manual-place');
  let picker = null, marker = null;
  const pickerStatus = document.querySelector('#place-picker-status');
  const pickerDetails = document.querySelector('.manual-place');
  function setPoint(lat, lon) {
    if (running) return;
    manual.elements.lat.value = Number(lat).toFixed(6);
    manual.elements.lon.value = Number(lon).toFixed(6);
    manual.elements.confirmed.checked = false;
    if (picker) {
      marker?.destroy();
      marker = new window.mapgl.CircleMarker(picker, { coordinates: [lon, lat], diameter: 16, color: '#326951', strokeColor: '#ffffff', strokeWidth: 3 });
    }
    pickerStatus.textContent = `Выбрана точка ${Number(lat).toFixed(5)}, ${Number(lon).toFixed(5)}. Проверьте место и подтвердите его ниже.`;
  }
  pickerDetails.addEventListener('toggle', () => {
    if (!pickerDetails.open) return;
    if (!picker) {
      picker = window.FlatsMap?.(document.querySelector('#place-picker-map'), [131.8855, 43.1155], state => {
        if (state === 'error') pickerStatus.textContent = 'Не удалось загрузить карту 2ГИС. Можно ввести координаты вручную.';
      }, 12);
      if (!picker) { pickerStatus.textContent = 'Карта 2ГИС недоступна. Проверьте подключение, ключ и поддержку WebGL или введите координаты вручную.'; return; }
      picker.on('click', event => setPoint(event.lngLat[1], event.lngLat[0]));
    }
    requestAnimationFrame(() => picker.invalidateSize());
  });
  document.querySelector('#manual-place').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return; const d = new FormData(form); if (addPlace({ id: uid(), name: d.get('name').trim(), lat: d.get('lat'), lon: d.get('lon') })) { form.reset(); pickerDetails.open = false; } });
  document.querySelector('#add-leg').addEventListener('click', () => { const prev = schedule.legs.at(-1); schedule.legs.push({ id: uid(), from: prev?.to || 'home', to: prev?.to && prev.to !== 'home' ? 'home' : schedule.places[0].id, days: prev ? [...prev.days] : [0,1,2,3,4], departure: '', max_minutes: null, max_transfers: null }); changed(); render(); legsList.lastElementChild.querySelector('select').focus(); });
  function valid() {
    if (!schedule.places.length || !schedule.legs.length) throw new Error('Добавьте место и хотя бы одну поездку.');
    if (schedule.legs.some(l => l.from === l.to || !l.days.length || !l.departure)) throw new Error('У каждой поездки должны отличаться начало и конец; выберите дни и время выезда.');
    if ([...legsList.querySelectorAll('input')].some(input => !input.checkValidity())) throw new Error('Проверьте значения времени и ограничений.');
  }
  async function save() { valid(); const savedRevision = revision; const data = await api('/schedule', { method: 'POST', body: JSON.stringify(schedule) }); if (revision === savedRevision) { version = data.version; dirty = false; } return data; }
  for (const [id, index] of [['quick-out', 0], ['quick-back', 1]]) {
    document.querySelector(`#${id}`).addEventListener('change', event => { schedule.legs[index].departure = event.target.value; changed(); render(); });
  }
  function setRunning(value, cancellable = false) {
    running = value;
    root.querySelectorAll('button:not(#cancel-trips), input, select').forEach(el => el.disabled = value);
    document.querySelector('#cancel-trips').hidden = !cancellable;
    document.querySelector('#trip-progress').hidden = !cancellable;
    if (!value) document.querySelector('#add-leg').disabled = !schedule.places.length;
  }
  function showJob(data) { const progress = document.querySelector('#trip-progress'); progress.max = data.total || 1; progress.value = data.done || 0; say(`Неделя ${data.week_start || ''} — ${data.week_end || ''}. Рассчитано ${data.done || 0} из ${data.total || 0} участков.`); }
  function cancelJob() {
    if (!cancellation) {
      cancellation = api(`/${encodeURIComponent(job)}/cancel`, { method: 'POST', body: '{}' });
      // The running step may settle later; finally reports cancellation errors.
      cancellation.catch(() => {});
    }
    return cancellation;
  }
  document.addEventListener('decisionschange', () => {
    revision++; estimate = null;
    document.querySelector('#partial-trips').hidden = true;
    document.querySelector('#trip-result-link').hidden = true;
    say('Решения по квартирам изменились. Подберите квартиры заново.');
  });
  async function run(partial = false) {
    if (running) return;
    setRunning(true);
    const currentRevision = revision;
    let choiceKey;
    const decisions = () => {
      try { return window.FlatsDecisions.current(); }
      catch { throw new Error('Не удалось прочитать решения в браузере. Проверьте данные сайта и повторите подбор.'); }
    };
    const stillCurrent = () => currentRevision === revision && choiceKey === JSON.stringify(decisions());
    document.querySelector('#trip-result-link').hidden = true;
    try {
      const choices = decisions();
      choiceKey = JSON.stringify(choices);
      if (!partial || !estimate || estimate.choiceKey !== choiceKey) {
        await save();
        if (!stillCurrent()) return;
        estimate = await api('/estimate', { method: 'POST', body: JSON.stringify({ scope: 'all', decisions: choices }) });
        if (!stillCurrent()) return;
        estimate.choiceKey = choiceKey;
        const summary = `${estimate.candidates} квартир, ${estimate.total_tasks} участков недели. Потребуется до ${estimate.unique_queries} запросов; осталось ${estimate.remaining_session} на сессию и ${estimate.remaining_global} на сервер${estimate.remaining_network === undefined ? '' : `, ${estimate.remaining_network} для подключения`}.`;
        if (!estimate.candidates) { say('Нет квартир, подходящих под условия и ваши решения. Измените условия по ссылке выше.'); return; }
        if (!estimate.sufficient_budget) {
          if (Math.min(estimate.remaining_session, estimate.remaining_global, estimate.remaining_network ?? Infinity) <= 0) say(`${summary} Лимит исчерпан. Попробуйте завтра или измените график.`, true);
          else { say(`${summary} На полный расчёт лимита недостаточно. Можно рассчитать доступную часть; остальные результаты будут неполными.`); document.querySelector('#partial-trips').hidden = false; }
          return;
        }
      }
      document.querySelector('#partial-trips').hidden = true;
      const data = await api('/start', { method: 'POST', body: JSON.stringify({ version, scope: 'all', decisions: choices }) });
      job = data.id;
      if (!stillCurrent()) return;
      let current = data;
      setRunning(true, true); showJob(current);
      while (current.status === 'running' && stillCurrent()) {
        current = await api(`/${encodeURIComponent(job)}/step`, { method: 'POST', body: '{}' });
        if (!stillCurrent()) return;
        showJob(current);
      }
      if (!stillCurrent()) return;
      estimate = null; job = null;
      if (current.status === 'stale') { say('График изменился в другой вкладке. Обновите страницу и повторите подбор.', true); return; }
      say(!current.total ? 'Для поездок не нашлось точек с пригодными координатами. Результаты в подборке неполные.' : current.status === 'complete' ? 'Расчёт завершён. Откройте подборку: сначала полностью рассчитанные подходящие квартиры; неполные результаты отмечены отдельно.' : 'Расчёт остановлен или ограничен. Неполные результаты отмечены в подборке.');
      document.querySelector('#trip-result-link').hidden = false;
    } catch (error) { if (currentRevision === revision) say(error.message, true); }
    finally {
      if (job) {
        try {
          await cancelJob();
          if (cancelRevision === revision) { say('Расчёт остановлен. Доступны только уже рассчитанные участки.'); document.querySelector('#trip-result-link').hidden = false; }
        } catch (error) { if (currentRevision === revision || cancelRevision === revision) say(error.message, true); }
        job = null;
      }
      cancelRevision = null;
      cancellation = null;
      document.querySelector('#cancel-trips').disabled = false;
      setRunning(false);
    }
  }
  document.querySelector('#start-trips').addEventListener('click', () => run());
  document.querySelector('#partial-trips').addEventListener('click', () => run(true));
  document.querySelector('#cancel-trips').addEventListener('click', () => {
    if (!job || cancelRevision !== null) return;
    cancelRevision = ++revision; estimate = null;
    document.querySelector('#cancel-trips').disabled = true;
    say('Останавливаем расчёт…');
    cancelJob();
  });
  setRunning(true);
  api('/schedule').then(data => { schedule = data.schedule || { places: [], legs: [] }; version = data.version; render(); }).catch(error => say(error.message, true)).finally(() => setRunning(false));
  window.addEventListener('beforeunload', event => { if (dirty || running) { event.preventDefault(); event.returnValue = ''; } });
})();
