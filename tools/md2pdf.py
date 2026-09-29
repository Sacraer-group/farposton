# -*- coding: utf-8 -*-
"""Конвертер документов прогноза в PDF — для печати и пересылки клиенту.

Отличие от md2html.py: там страница для чтения с экрана (тёмная тема, ширина
780px), здесь — бумага A4 с полями, светлая всегда, таблицы не рвутся по строкам,
шапка с брендом Прогноз заказов на первой странице.

Печатает headless-браузером (Chrome, иначе Edge) — сторонних библиотек не нужно.

Запуск: PYTHONUTF8=1 py prognoz/tools/md2pdf.py docs/исходящие/Файл.md [ещё.md]
Выход:  рядом с исходником, <имя>.pdf
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]          # …\prognoz

BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

CSS = """
  @page { size: A4; margin: 18mm 16mm 20mm; }
  * { box-sizing: border-box; }
  body { margin:0; color:#1f2328; background:#fff;
         font:11.5pt/1.55 Georgia,"Times New Roman",serif; }
  .brand { display:flex; align-items:baseline; gap:10px; margin:0 0 18px;
           padding-bottom:10px; border-bottom:2px solid #D42B2B; }
  .brand .name { font:700 15pt/1 "Segoe UI",system-ui,sans-serif; letter-spacing:.14em; }
  .brand .dot { color:#D42B2B; }
  .brand .sub { font:10pt/1 "Segoe UI",system-ui,sans-serif; color:#656d76;
                letter-spacing:.04em; }
  h1 { font-size:19pt; line-height:1.2; margin:0 0 14px; }
  h2 { font-size:14pt; margin:22px 0 8px; padding-top:8px; border-top:1px solid #e4e6ea;
       break-after:avoid; }
  h3 { font-size:12pt; margin:16px 0 6px; break-after:avoid; }
  p, li { orphans:3; widows:3; }
  li { margin-bottom:4px; }
  table { border-collapse:collapse; width:100%; margin:10px 0; break-inside:avoid;
          font:10pt/1.4 "Segoe UI",system-ui,sans-serif; }
  img { display:block; max-width:100%; height:auto; margin:10px auto;
        break-inside:avoid; }
  th, td { border-bottom:1px solid #e4e6ea; padding:5px 8px; text-align:left;
           vertical-align:top; }
  th { color:#656d76; font-weight:600; border-bottom:1.5px solid #c9ccd1; }
  tr:nth-child(even) td { background:#faf9f7; }
  code { font:9.5pt/1.4 Consolas,monospace; background:#f4f4f2; border:1px solid #e4e6ea;
         border-radius:3px; padding:0 3px; }
  pre { margin:10px 0; padding:9px 11px; white-space:pre-wrap; overflow-wrap:anywhere;
        background:#f4f4f2; border:1px solid #e4e6ea; border-radius:4px;
        break-inside:avoid; font:9pt/1.45 Consolas,monospace; }
  pre code { padding:0; border:0; background:transparent; font:inherit; }
  blockquote { border-left:3px solid #D42B2B; margin:12px 0; padding:2px 0 2px 12px;
               color:#4a5058; }
  hr { border:none; border-top:1px solid #e4e6ea; margin:20px 0; }
  strong { color:#111; }
  .foot { margin-top:26px; padding-top:8px; border-top:1px solid #e4e6ea;
          font:9pt/1.4 "Segoe UI",system-ui,sans-serif; color:#656d76; }
"""

TPL = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>{title}</title>
<base href="{base_uri}">
<style>{css}</style></head><body>
<div class="brand"><span class="name">T<span class="dot">\u25cf</span>KYO MARKET</span>
<span class="sub">прогноз заказов \u00b7 \u0421\u0438\u043d\u0445\u0440\u043e\u043d</span></div>
{body}
<div class="foot">Подготовлено компанией «аналитической системы» для сети Прогноз заказов.</div>
</body></html>"""


def find_browser() -> str:
    for b in BROWSERS:
        if Path(b).exists():
            return b
    raise SystemExit("не найден Chrome/Edge для печати PDF")


def convert(md_path: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        text, extensions=["tables", "sane_lists", "attr_list", "fenced_code"]
    )
    title = md_path.stem.replace("_", " ")
    base_uri = md_path.resolve().parent.as_uri().rstrip("/") + "/"
    html = TPL.format(title=title, base_uri=base_uri, css=CSS, body=body)

    tmp = Path(tempfile.mkdtemp(prefix="md2pdf_")) / (md_path.stem + ".html")
    tmp.write_text(html, encoding="utf-8")
    out = md_path.with_suffix(".pdf")

    cmd = [find_browser(), "--headless", "--disable-gpu", "--no-sandbox",
           "--allow-file-access-from-files",
           "--no-pdf-header-footer", f"--print-to-pdf={out}", tmp.as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not out.exists():
        raise SystemExit(f"PDF не создан: {r.stderr[-500:]}")
    print(f"→ {out}  ({out.stat().st_size // 1024} КБ)")
    return out


def main() -> int:
    args = [Path(a) for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        return 2
    for a in args:
        p = a if a.is_absolute() else (ROOT / a if (ROOT / a).exists() else a)
        if not p.exists():
            print(f"нет файла: {p}")
            return 1
        convert(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
