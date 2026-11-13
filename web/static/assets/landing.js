'use strict';
(() => {
  const form = document.getElementById('order-example');
  if (!form) return;
  const inputs = Array.from(form.querySelectorAll('input[type="number"]'));
  const total = document.getElementById('order-total');
  const status = document.getElementById('edit-status');
  const initial = inputs.map(input => Number(input.defaultValue));
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let lastTotal = null;
  let totalMotion;
  reduced.addEventListener('change', () => {
    if (reduced.matches && totalMotion) totalMotion.cancel();
  });
  function update() {
    const valid = inputs.every(input => input.value !== '' && input.validity.valid && Number.isInteger(Number(input.value)));
    for (const input of inputs) {
      input.setAttribute('aria-invalid', String(input.value === '' || !input.validity.valid || !Number.isInteger(Number(input.value))));
    }
    if (!valid) {
      if (totalMotion) totalMotion.cancel();
      total.textContent = 'Проверьте числа';
      total.classList.add('invalid-total');
      status.textContent = 'Допустимо от 0 до 999 шт.';
      return;
    }
    const values = inputs.map(input => Number(input.value));
    const sum = values.reduce((sum, value) => sum + value, 0);
    const changed = lastTotal !== null && sum !== lastTotal;
    lastTotal = sum;
    total.classList.remove('invalid-total');
    total.replaceChildren(document.createTextNode(String(sum) + ' '));
    const unit = document.createElement('span');
    unit.textContent = 'шт.';
    total.append(unit);
    if (totalMotion) totalMotion.cancel();
    if (changed && !reduced.matches && typeof total.animate === 'function') {
      totalMotion = total.animate([{ transform: 'translateY(5px)', opacity: .65 }, { transform: 'translateY(0)', opacity: 1 }], { duration: 220, easing: 'ease-out' });
    }
    status.textContent = values.every((value, index) => value === initial[index]) ? 'Исходная заявка' : 'Учебная правка';
  }
  form.addEventListener('input', update);
  form.addEventListener('reset', () => {
    inputs.forEach(input => { input.value = input.defaultValue; });
    update();
  });
  form.addEventListener('submit', event => event.preventDefault());
  update();
})();

(() => {
  const demo = document.getElementById('process-demo');
  const play = document.getElementById('play-process');
  const status = document.getElementById('process-status');
  if (!demo || !play || !status) return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let timer;
  const stages = {
    history: '1. История продаж: количества по дням',
    chart: '2. Видим спрос по дням — основа для рекомендации',
    result: '3. Количества для трёх точек — итог примера'
  };
  function setPhase(phase) {
    demo.dataset.phase = phase;
    status.textContent = stages[phase];
    demo.querySelectorAll('.data-stage').forEach(stage => {
      stage.setAttribute('aria-hidden', String(!stage.classList.contains(phase + '-stage')));
    });
  }
  function finish() {
    clearTimeout(timer);
    setPhase('result');
    play.disabled = false;
    play.textContent = reduced.matches ? 'Показать итог примера' : 'Посмотреть ещё раз ↺';
  }
  play.disabled = false;
  play.addEventListener('click', () => {
    clearTimeout(timer);
    if (reduced.matches) { finish(); return; }
    play.disabled = true;
    play.textContent = 'Показываем порядок работы…';
    setPhase('history');
    timer = setTimeout(() => {
      setPhase('chart');
      timer = setTimeout(finish, 2400);
    }, 1900);
  });
  reduced.addEventListener('change', () => { if (reduced.matches) finish(); });
  if (reduced.matches) finish();
})();

(() => {
  const link = document.getElementById('show-route');
  const hero = document.getElementById('forecast-hero');
  if (!link || !hero) return;
  link.addEventListener('click', () => hero.classList.add('route-active'));
})();

(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  if (reduced.matches || typeof window.IntersectionObserver !== 'function') return;
  const elements = Array.from(document.querySelectorAll('[data-reveal]'));
  const observer = new window.IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.remove('reveal-pending');
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    }
  }, { threshold: 0, rootMargin: '0px 0px -24px 0px' });
  for (const element of elements) {
    // Content is visible without JS; only sections below the viewport are armed.
    if (element.getBoundingClientRect().top > window.innerHeight * 0.95) {
      element.classList.add('reveal-pending');
      observer.observe(element);
    }
  }
  document.addEventListener('focusin', event => {
    const element = event.target.closest('[data-reveal]');
    if (!element) return;
    element.classList.remove('reveal-pending');
    observer.unobserve(element);
  });
  reduced.addEventListener('change', () => {
    if (!reduced.matches) return;
    for (const element of elements) element.classList.remove('reveal-pending');
    observer.disconnect();
  });
})();
