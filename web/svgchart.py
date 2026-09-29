# -*- coding: utf-8 -*-
"""SVG-графики без зависимостей — копия tools/svgchart.py под бренд веб-сервиса ПРОГНОЗ.

Отличия от оригинала:
- цвета — контрастные CSS-переменные из static/assets/app.css; один и тот же
  порядок серий одинаково читается в светлой и тёмной темах;
- цвет дополнен штрихом линии и формой маркера: график остаётся различимым при
  дальтонизме, печати в оттенках серого и на небольшом экране;
- шрифт — системный Segoe UI, как на страницах сервиса;
- lines() сохраняет порядок X-подписей как их передали (в оригинале сортировка
  лексикографически ломала «31.08 → 01.09»);
- пустые данные не роняют график, а честно рисуют «нет данных».
"""
from __future__ import annotations

from . import paths  # noqa: F401  — кладёт prognoz/src в sys.path ДО импорта ядра

from prognoz.facts.fmt import fmt_ru

# Контрастная палитра серий (Okabe–Ito-подобная): модель/прогноз — синий,
# формула/заявка — оранжевый, сотрудник/продажи — зелёный, эталон — фиолетовый.
# Hex-фолбэки нужны, чтобы SVG оставался читаемым и вне страниц сервиса.
PALETTE = ("var(--chart-1,#2563EB)", "var(--chart-2,#D97706)",
           "var(--chart-3,#059669)", "var(--chart-4,#7C3AED)",
           "var(--chart-5,#0891B2)", "var(--chart-6,#DB2777)")
# Штрихи — дополнительный канал различения, не зависящий от цвета.
LINE_DASHES = ("", "9 5", "3 4", "12 4 3 4", "2 5", "8 3 2 3")
# В парных барах порядок серий (человек, модель): человек — оранжевый,
# модель — синий.
HBAR_COLORS = ("var(--chart-2,#D97706)", "var(--chart-1,#2563EB)")
AXIS = "var(--chart-axis,#6E6E6E)"
GRID = "var(--chart-grid,#E8E4E0)"
TEXT = "var(--chart-text,#141414)"
FONT = "font-family=\"'Segoe UI',system-ui,sans-serif\""


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 0.1
    for step in (mag, 2 * mag, 2.5 * mag, 5 * mag, 10 * mag):
        if raw <= step:
            break
    t0 = int(lo / step) * step
    out = []
    t = t0
    while t <= hi + step / 2:
        if t >= lo - step / 2:
            out.append(round(t, 10))
        t += step
    return out


def _frame(w: int, h: int, title: str, inner: str) -> str:
    accessible_title = _esc(title)
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{accessible_title}" '
             f'xmlns="http://www.w3.org/2000/svg" style="max-width:100%;height:auto">'
            f'<title>{accessible_title}</title>'
            f'<text x="12" y="20" {FONT} font-size="15" font-weight="bold" '
            f'fill="{TEXT}">{accessible_title}</text>{inner}</svg>')


def _empty(title: str, w: int = 760) -> str:
    return _frame(w, 90, title, f'<text x="12" y="60" {FONT} font-size="13" '
                                f'fill="{AXIS}">нет данных</text>')


def hbars(rows: list[tuple[str, dict]], *, title: str, unit: str = "%",
          series: tuple[str, str] = ("человек", "модель"),
          keys: tuple[str, str] = ("human", "model"),
          ref_key: str | None = None, w: int = 760) -> str:
    """Горизонтальные пары баров (лестница). rows: [(подпись, {human, model[, ref]})]."""
    if not rows:
        return _empty(title, w)
    row_h, gap, left = 46, 18, 210
    h = 46 + len(rows) * (row_h + gap)
    vmax = max(max(r.get(keys[0]) or 0, r.get(keys[1]) or 0,
                   (r.get(ref_key) or 0) if ref_key else 0)
               for _n, r in rows) * 1.12 or 1
    span = w - left - 70
    parts = []
    y = 40
    for name, r in rows:
        parts.append(f'<text x="{left - 10}" y="{y + 26}" {FONT} font-size="12" fill="{TEXT}" '
                     f'text-anchor="end">{_esc(name)}</text>')
        for i, (key, label) in enumerate(((keys[0], series[0]), (keys[1], series[1]))):
            v = r.get(key)
            if v is None:
                continue
            bw = max(2, abs(v) / vmax * span)
            by = y + 4 + i * 20
            parts.append(f'<rect x="{left}" y="{by}" width="{bw:.1f}" height="16" '
                          f'rx="3" fill="{HBAR_COLORS[i]}" '
                         f'opacity="{0.82 if i == 0 else 1.0}"><title>'
                         f'{_esc(name)} · {_esc(label)}: {fmt_ru(v)}{_esc(unit)}'
                         f'</title></rect>')
            parts.append(f'<text x="{left + bw + 6:.1f}" y="{by + 13}" {FONT} font-size="12" '
                         f'fill="{TEXT}">{fmt_ru(v)}{unit} {label}</text>')
        if ref_key and r.get(ref_key) is not None:
            rx = left + r[ref_key] / vmax * span
            parts.append(f'<line x1="{rx:.1f}" y1="{y}" x2="{rx:.1f}" y2="{y + row_h}" '
                         f'stroke="{AXIS}" stroke-dasharray="4 3"/>')
            parts.append(f'<text x="{rx:.1f}" y="{y - 2}" {FONT} font-size="10" '
                         f'fill="{AXIS}" text-anchor="middle">предел {fmt_ru(r[ref_key])}{unit}</text>')
        y += row_h + gap
    return _frame(w, h, title, "".join(parts))


def bars(labels: list[str], values: list[float], *, title: str, unit: str = "",
         w: int = 760, h: int = 300, fmt_digits: int = 1) -> str:
    if not values:
        return _empty(title, w)
    left, bottom, top = 64, 46, 40
    span_x, span_y = w - left - 20, h - top - bottom
    vmax = max(max(values), 0) * 1.12 or 1
    n = len(values)
    bw = span_x / max(n, 1) * 0.7
    parts = []
    for t in _nice_ticks(0, vmax):
        ty = h - bottom - t / vmax * span_y
        parts.append(f'<line x1="{left}" y1="{ty:.1f}" x2="{w - 20}" y2="{ty:.1f}" '
                     f'stroke="{GRID}" stroke-width="0.6"/>')
        parts.append(f'<text x="{left - 8}" y="{ty + 4:.1f}" {FONT} font-size="10" '
                     f'fill="{AXIS}" text-anchor="end">{fmt_ru(t, 0 if t == int(t) else 1)}</text>')
    for i, (lbl, v) in enumerate(zip(labels, values)):
        x = left + span_x * (i + 0.15) / n
        bh = v / vmax * span_y
        parts.append(f'<rect x="{x:.1f}" y="{h - bottom - bh:.1f}" width="{bw:.1f}" '
                     f'height="{bh:.1f}" rx="2" fill="{PALETTE[0]}"><title>'
                     f'{_esc(lbl)}: {fmt_ru(v, fmt_digits)}{_esc(unit)}'
                     f'</title></rect>')
        if n <= 16:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{h - bottom - bh - 4:.1f}" {FONT} '
                         f'font-size="10" fill="{TEXT}" text-anchor="middle">'
                         f'{fmt_ru(v, fmt_digits)}</text>')
        step = max(1, n // 14)
        if i % step == 0:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{h - bottom + 16}" {FONT} '
                         f'font-size="10" fill="{AXIS}" text-anchor="middle">{_esc(lbl)}</text>')
    if unit:
        parts.append(f'<text x="{left}" y="{top - 8}" {FONT} font-size="10" '
                     f'fill="{AXIS}">{_esc(unit)}</text>')
    return _frame(w, h, title, "".join(parts))


def lines(series: dict[str, list[tuple[str, float]]], *, title: str, unit: str = "",
          w: int = 760, h: int = 330, y0: float | None = 0.0) -> str:
    """Линии по общим X-подписям. series: {имя: [(x_label, y), …]}.
    Порядок X — как передали (даты подавать уже отсортированными)."""
    xs: list[str] = []
    for pts in series.values():
        for x, _ in pts:
            if x not in xs:
                xs.append(x)
    ys = [y for pts in series.values() for _x, y in pts if y == y]
    if not xs or not ys:
        return _empty(title, w)
    left, bottom, top = 64, 46, 56
    span_x, span_y = w - left - 20, h - top - bottom
    bounds = ys + ([y0] if y0 is not None else [])
    lo, hi_raw = min(bounds), max(bounds)
    if hi_raw == lo:
        # В том числе единичный отрицательный час (только возврат) и все нули.
        pad = max(abs(hi_raw) * 0.08, 1.0)
        hi = hi_raw + pad
    else:
        hi = hi_raw + (hi_raw - lo) * 0.08
    parts = []
    for t in _nice_ticks(lo, hi):
        ty = h - bottom - (t - lo) / (hi - lo) * span_y
        parts.append(f'<line x1="{left}" y1="{ty:.1f}" x2="{w - 20}" y2="{ty:.1f}" '
                     f'stroke="{GRID}" stroke-width="0.6"/>')
        parts.append(f'<text x="{left - 8}" y="{ty + 4:.1f}" {FONT} font-size="10" '
                     f'fill="{AXIS}" text-anchor="end">{fmt_ru(t, 0 if abs(t) >= 10 else 1)}</text>')

    def sx(i: int) -> float:
        return left + span_x * i / max(len(xs) - 1, 1)

    step = max(1, len(xs) // 10)
    for i, x in enumerate(xs):
        if i % step == 0:
            parts.append(f'<text x="{sx(i):.1f}" y="{h - bottom + 16}" {FONT} '
                         f'font-size="10" fill="{AXIS}" text-anchor="middle">{_esc(x)}</text>')
    for k, (name, pts) in enumerate(series.items()):
        col = PALETTE[k % len(PALETTE)]
        lut = dict(pts)
        coords = [(sx(i), h - bottom - (lut[x] - lo) / (hi - lo) * span_y)
                  for i, x in enumerate(xs) if x in lut and lut[x] == lut[x]]
        if not coords:
            continue
        dash = LINE_DASHES[k % len(LINE_DASHES)]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        d = "M" + " L".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
        parts.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2.4" '
                     f'stroke-linejoin="round" stroke-linecap="round"{dash_attr}/>')
        for cx, cy in coords:
            # coords может пропускать X с дырой; находим ближайший исходный X по координате.
            point_index = min(range(len(xs)), key=lambda j: abs(sx(j) - cx))
            x_label = xs[point_index]
            value = lut[x_label]
            tip = _esc(f"{x_label} · {name}: {fmt_ru(value)}{unit}")
            marker = k % 4
            if marker == 0:
                shape = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" '
                         f'fill="{col}" stroke="var(--paper,#FFFFFF)" stroke-width="1"/>')
            elif marker == 1:
                shape = (f'<rect x="{cx - 3.4:.1f}" y="{cy - 3.4:.1f}" width="6.8" '
                         f'height="6.8" rx="1" fill="{col}" stroke="var(--paper,#FFFFFF)" '
                         f'stroke-width="1"/>')
            elif marker == 2:
                shape = (f'<polygon points="{cx:.1f},{cy - 4:.1f} {cx + 4:.1f},{cy:.1f} '
                         f'{cx:.1f},{cy + 4:.1f} {cx - 4:.1f},{cy:.1f}" fill="{col}" '
                         f'stroke="var(--paper,#FFFFFF)" stroke-width="1"/>')
            else:
                shape = (f'<polygon points="{cx:.1f},{cy - 4.2:.1f} {cx + 4.2:.1f},{cy + 3.4:.1f} '
                         f'{cx - 4.2:.1f},{cy + 3.4:.1f}" fill="{col}" '
                         f'stroke="var(--paper,#FFFFFF)" stroke-width="1"/>')
            # Прозрачная зона 16×16 упрощает наведение, особенно на плотном графике.
            parts.append(f'<g><title>{tip}</title>{shape}<circle cx="{cx:.1f}" cy="{cy:.1f}" '
                         f'r="8" fill="transparent"/></g>')
        lx = 12 + (k % 3) * 250
        ly = 34 + (k // 3) * 16
        parts.append(f'<line x1="{lx}" y1="{ly - 7}" x2="{lx + 14}" y2="{ly - 7}" '
                     f'stroke="{col}" stroke-width="3" stroke-linecap="round"{dash_attr}/>')
        parts.append(f'<text x="{lx + 17}" y="{ly}" {FONT} font-size="11" '
                     f'fill="{TEXT}">{_esc(name)}</text>')
    if unit:
        parts.append(f'<text x="{w - 20}" y="{top - 8}" {FONT} font-size="10" '
                     f'fill="{AXIS}" text-anchor="end">{_esc(unit)}</text>')
    return _frame(w, h, title, "".join(parts))
