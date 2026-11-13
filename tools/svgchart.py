# -*- coding: utf-8 -*-
"""SVG-графики без зависимостей: линии, бары, горизонтальные бары.

Почему свой генератор, а не matplotlib: программа по договору переезжает на сервер
клиента, и каждая зависимость — это то, что там придётся ставить и обновлять.
Наши графики простые (три типа), 250 строк дешевле пакета.

Цвета — только CSS-переменные из tools/md2html.py (--accent, --muted, --line,
--text), поэтому тёмная тема работает без единой строчки здесь.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prognoz.facts.fmt import fmt_ru      # noqa: E402

# палитра серий: первая — акцент, дальше фиксированные различимые
PALETTE = ("var(--accent)", "#4c78a8", "#59a14f", "#e15759", "#b07aa1", "#9c755f")
AXIS = "var(--muted)"
GRID = "var(--line)"
TEXT = "var(--text)"
FONT = 'font-family="Georgia, serif"'


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
    return (f'<svg viewBox="0 0 {w} {h}" role="img" '
            f'xmlns="http://www.w3.org/2000/svg" style="max-width:100%;height:auto">'
            f'<text x="12" y="20" {FONT} font-size="15" font-weight="bold" '
            f'fill="{TEXT}">{_esc(title)}</text>{inner}</svg>')


def hbars(rows: list[tuple[str, dict]], *, title: str, unit: str = "%",
          series: tuple[str, str] = ("человек", "модель"),
          keys: tuple[str, str] = ("human", "model"),
          ref_key: str | None = "floor", w: int = 760) -> str:
    """Горизонтальные пары баров (лестница). rows: [(подпись, {human, model, floor})]."""
    row_h, gap, left = 46, 18, 210
    h = 46 + len(rows) * (row_h + gap)
    vmax = max(max(r.get(keys[0], 0), r.get(keys[1], 0), r.get(ref_key or "", 0) or 0)
               for _n, r in rows) * 1.12
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
                         f'rx="3" fill="{PALETTE[i + (0 if i == 0 else 0)]}" '
                         f'opacity="{0.55 if i == 0 else 1.0}"/>')
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
                     f'height="{bh:.1f}" rx="2" fill="{PALETTE[0]}"/>')
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
    """Линии по общим X-подписям (месяцы). series: {имя: [(x_label, y), …]}."""
    left, bottom, top = 64, 46, 56
    span_x, span_y = w - left - 20, h - top - bottom
    xs: list[str] = []
    for pts in series.values():
        for x, _ in pts:
            if x not in xs:
                xs.append(x)
    xs.sort()
    ys = [y for pts in series.values() for _x, y in pts if y == y]
    lo = min(ys + ([y0] if y0 is not None else []))
    hi = max(ys) * 1.08 or 1
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
        d = "M" + " L".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
        parts.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2"/>')
        lx = 12 + (k % 3) * 250
        ly = 34 + (k // 3) * 16
        parts.append(f'<rect x="{lx}" y="{ly - 9}" width="12" height="3" fill="{col}"/>')
        parts.append(f'<text x="{lx + 17}" y="{ly}" {FONT} font-size="11" '
                     f'fill="{TEXT}">{_esc(name)}</text>')
    if unit:
        parts.append(f'<text x="{w - 20}" y="{top - 8}" {FONT} font-size="10" '
                     f'fill="{AXIS}" text-anchor="end">{_esc(unit)}</text>')
    return _frame(w, h, title, "".join(parts))
