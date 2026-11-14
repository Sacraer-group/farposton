(() => {
  const root = document.querySelector('#trip-app');
  if (!root) return;
  const status = document.querySelector('#trip-status');
  const placesList = document.querySelector('#trip-places');
  const legsList = document.querySelector('#trip-legs');
  const names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  let schedule = { places: [], legs: [] }, version = null, job = null, running = false, dirty = false, estimate = null;
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
    estimate = null;
    document.querySelector('#start-trips').textContent = 'Оценить расчёт';
    document.querySelector('#trip-result-link').hidden = true;
    say('График изменён. Сохраните его и запустите новый расчёт.');
  }
  function button(text, action, cls = 'button button-small button-outline') {
    const b = node('button', text, cls); b.type = 'button'; b.addEventListener('click', action); return b;
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
  }
  function addPlace(p) {
    if (schedule.places.some(a => a.id === String(p.id))) { say('Это место уже добавлено.', true); return; }
    schedule.places.push({ id: String(p.id || uid()), name: p.name, lat: Number(p.lat), lon: Number(p.lon), confirmed: true }); changed(); render();
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
  document.querySelector('#manual-place').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return; const d = new FormData(form); addPlace({ id: uid(), name: d.get('name').trim(), lat: d.get('lat'), lon: d.get('lon') }); form.reset(); });
  document.querySelector('#add-leg').addEventListener('click', () => { const prev = schedule.legs.at(-1); schedule.legs.push({ id: uid(), from: prev?.to || 'home', to: prev?.to && prev.to !== 'home' ? 'home' : schedule.places[0].id, days: prev ? [...prev.days] : [0,1,2,3,4], departure: '', max_minutes: null, max_transfers: null }); changed(); render(); legsList.lastElementChild.querySelector('select').focus(); });
  function valid() {
    if (!schedule.places.length || !schedule.legs.length) throw new Error('Добавьте место и хотя бы одну поездку.');
    if (schedule.legs.some(l => l.from === l.to || !l.days.length || !l.departure)) throw new Error('У каждой поездки должны отличаться начало и конец; выберите дни и время выезда.');
    if ([...legsList.querySelectorAll('input')].some(input => !input.checkValidity())) throw new Error('Проверьте значения времени и ограничений.');
  }
  async function save() { valid(); const data = await api('/schedule', { method: 'POST', body: JSON.stringify(schedule) }); version = data.version; dirty = false; return data; }
  document.querySelector('#save-schedule').addEventListener('click', async () => { try { await save(); say('График сохранён. Можно запустить подбор.'); } catch (error) { say(error.message, true); } });
  function setRunning(value) { running = value; root.querySelectorAll('button:not(#cancel-trips), input, select').forEach(el => el.disabled = value); document.querySelector('#cancel-trips').hidden = !value; document.querySelector('#trip-progress').hidden = !value; if (!value) document.querySelector('#add-leg').disabled = !schedule.places.length; }
  function showJob(data) { const progress = document.querySelector('#trip-progress'); progress.max = data.total || 1; progress.value = data.done || 0; say(`Неделя ${data.week_start || ''} — ${data.week_end || ''}. Рассчитано ${data.done || 0} из ${data.total || 0} участков.`); }
  document.addEventListener('decisionschange', () => { estimate = null; document.querySelector('#start-trips').textContent = 'Оценить расчёт'; });
  document.querySelector('#trip-scope').addEventListener('change', () => { estimate = null; document.querySelector('#start-trips').textContent = 'Оценить расчёт'; say('Область подбора изменена. Повторите оценку расхода запросов.'); });
  document.querySelector('#start-trips').addEventListener('click', async () => {
    try {
      await save();
      const scope = document.querySelector('#trip-scope').value;
      const decisions = window.FlatsDecisions.current();
      const choiceKey = JSON.stringify(decisions);
      if (!estimate || estimate.version !== version || estimate.scope !== scope || estimate.choiceKey !== choiceKey) {
        estimate = await api('/estimate', { method: 'POST', body: JSON.stringify({ scope, decisions }) });
        estimate.choiceKey = choiceKey;
        const summary = `${estimate.candidates} квартир, ${estimate.total_tasks} участков недели. Потребуется до ${estimate.unique_queries} запросов; осталось ${estimate.remaining_session} на сессию и ${estimate.remaining_global} на сервер${estimate.remaining_network === undefined ? "" : `, ${estimate.remaining_network} для подключения`}.`;
        if (!estimate.sufficient_budget) { estimate = null; say(`${summary} Для полного расчёта недостаточно лимита: сократите число квартир, дней или поездок.`, true); return; }
        document.querySelector('#start-trips').textContent = 'Запустить расчёт';
        say(`${summary}${estimate.unresolved_tasks ? ` У ${estimate.unresolved_tasks} участков нет пригодных координат; они останутся неполными.` : ''} Проверьте оценку и нажмите «Запустить расчёт».`);
        return;
      }
      setRunning(true); const data = await api('/start', { method: 'POST', body: JSON.stringify({ version, scope, decisions }) }); job = data.id; let current = data; showJob(current);

      while (running && current.status === 'running') { current = await api(`/${encodeURIComponent(job)}/step`, { method: 'POST', body: '{}' }); showJob(current); await new Promise(resolve => setTimeout(resolve, 80)); }
      setRunning(false); estimate = null; document.querySelector('#start-trips').textContent = 'Оценить расчёт'; say(current.status === 'complete' ? 'Расчёт завершён. Откройте подборку: сначала полностью рассчитанные подходящие квартиры.' : 'Расчёт остановлен или ограничен. Неполные результаты отмечены в подборке.'); document.querySelector('#trip-result-link').hidden = false;
    } catch (error) { setRunning(false); say(error.message, true); }
  });
  document.querySelector('#cancel-trips').addEventListener('click', async () => { running = false; try { if (job) await api(`/${encodeURIComponent(job)}/cancel`, { method: 'POST', body: '{}' }); say('Расчёт остановлен. Доступны только уже рассчитанные участки.'); document.querySelector('#trip-result-link').hidden = false; } catch (error) { say(error.message, true); } finally { setRunning(false); } });
  api('/schedule').then(data => { schedule = data.schedule || { places: [], legs: [] }; version = data.version; render(); }).catch(error => say(error.message, true));
  window.addEventListener('beforeunload', event => { if (dirty || running) { event.preventDefault(); event.returnValue = ''; } });
})();
