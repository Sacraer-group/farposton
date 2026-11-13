# -*- coding: utf-8 -*-
"""CLI калькулятора: py -m prognoz.facts [--block=имя] [--list] [--no-dryrun]"""
from __future__ import annotations

import sys
from datetime import date

from prognoz.facts import blocks  # noqa: F401 — регистрирует блоки через декоратор
from prognoz.facts.context import OUTPUT, build_context
from prognoz.facts.registry import BLOCKS, Registry


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--list" in args:
        for name in sorted(BLOCKS):
            print(name)
        return 0

    only = [a.split("=", 1)[1] for a in args if a.startswith("--block=")]
    need_dryrun = "--no-dryrun" not in args

    ctx = build_context(need_dryrun=need_dryrun)
    reg = Registry()
    failed = []
    try:
        for name, fn in sorted(BLOCKS.items()):
            if only and name not in only:
                continue
            try:
                fn(ctx, reg)
                print(f"✓ {name}")
            except Exception as e:                       # noqa: BLE001
                # Один упавший блок не должен обнулять остальные факты: реестр
                # пишется частичным, но выход ненулевой — прогон считается неудачным.
                failed.append(name)
                print(f"✗ {name}: {e}")
    finally:
        ctx.close()

    version = date.today().isoformat()
    # Частичный прогон (--block=…) обновляет только свои факты: полный файл
    # не затирается — иначе `--block=economics` оставлял бы от реестра четверть.
    merge_from = (OUTPUT / "facts.json") if only else None
    path = reg.write(OUTPUT, version=version, data_stamp=ctx.data_stamp,
                     merge_from=merge_from, append_history=not only)
    print(f"\nфактов: {len(reg.ids())}{' (+ мерж с прежними)' if only else ''} → {path}")
    if failed:
        print(f"УПАВШИЕ БЛОКИ: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
