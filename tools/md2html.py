# -*- coding: utf-8 -*-
"""Конвертер документов прогноза в HTML для чтения (сотрудник не открывает .md на ПК).

Кладёт готовые страницы в prognoz/docs/html/ — оттуда их можно слать в чат.
Запуск: PYTHONUTF8=1 py prognoz/tools/md2html.py        (все .md из docs/)
        PYTHONUTF8=1 py prognoz/tools/md2html.py <файл.md> ...
"""
import sys
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parents[1] / "docs"
OUT = DOCS / "html"

CSS = """
  :root { --bg:#fafaf8; --text:#1f2328; --muted:#656d76; --card:#fff; --line:#e4e6ea; --accent:#b4552d; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14161a; --text:#e6e8eb; --muted:#9aa2ac; --card:#1c1f24; --line:#2c3138; --accent:#e08d5f; }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--text);
         font:16px/1.65 Georgia, "Times New Roman", serif; }
  main { max-width: 780px; margin: 0 auto; }
  h1 { font-size:28px; line-height:1.25; }
  h2 { font-size:21px; margin-top:36px; padding-top:16px; border-top:1px solid var(--line); }
  h3 { font-size:17px; }
  a { color: var(--accent); }
  code { font: 13.5px/1.5 Consolas, monospace; background: var(--card); border:1px solid var(--line);
         border-radius:4px; padding:0 4px; }
  blockquote { border-left:3px solid var(--accent); margin:14px 0; padding:2px 0 2px 14px; color:var(--muted); }
  .tablewrap { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-size:14px; font-family:system-ui,sans-serif; margin:12px 0; }
  th, td { border-bottom:1px solid var(--line); padding:7px 10px; text-align:left; vertical-align:top; }
  th { color:var(--muted); font-weight:600; }
  li { margin-bottom:5px; }
  hr { border:none; border-top:1px solid var(--line); margin:28px 0; }
"""

TPL = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{css}</style></head>
<body><main>{body}</main></body></html>"""


def convert(src: Path) -> Path:
    text = src.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    body = body.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")
    title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("# ")), src.stem)
    dst = OUT / (src.stem + ".html")
    dst.write_text(TPL.format(title=title, css=CSS, body=body), encoding="utf-8")
    return dst


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:]
    # Архив и замороженные снимки в html НЕ гоним: их старые числа получали бы
    # свежую дату файла и лежали бы вперемешку с живыми доками — ровно так
    # читатель и «теряет инфу» (находка ревизии 10.08).
    skip = ("архив", "замеры")
    sources = [Path(a) if Path(a).is_absolute() else DOCS / a for a in args] or [
        p for p in sorted(DOCS.rglob("*.md"))
        if not any(part in skip for part in p.relative_to(DOCS).parts)]
    for src in sources:
        dst = convert(src)
        print(f"{src.relative_to(DOCS)} -> html/{dst.name} ({dst.stat().st_size:,} б)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
