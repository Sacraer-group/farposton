# -*- coding: utf-8 -*-
"""Проверка окружения: версии совпадают с прод-сервером.

Первый шаг онбординга: модель model.joblib обучена конкретной версией sklearn,
и расхождение версий даёт либо отказ загрузки, либо тихо другие числа.

Запуск: PYTHONUTF8=1 py tools/check_env.py
"""
import sys

EXPECTED = {
    "python": "3.12",          # префикс: 3.12.x
    "sklearn": "1.9.0",
    "pandas": "3.0.5",
    "duckdb": "1.5.4",
    "joblib": "1.5.3",
}


def main() -> int:
    problems = []
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    if py != EXPECTED["python"]:
        problems.append(f"Python {py} — на проде {EXPECTED['python']}.10")
    for mod, want in list(EXPECTED.items())[1:]:
        try:
            m = __import__(mod)
        except ImportError:
            problems.append(f"{mod} не установлен (pip install -r web/requirements.txt)")
            continue
        got = getattr(m, "__version__", "?")
        if got != want:
            problems.append(f"{mod} {got} — на проде {want}")
    if problems:
        print("Окружение расходится с прод-сервером:")
        for p in problems:
            print(f"  ✗ {p}")
        print("\nМодель joblib и результаты расчётов могут не сойтись с эталонами.")
        return 1
    print("Окружение совпадает с прод-сервером: "
          + ", ".join(f"{k} {v}" for k, v in EXPECTED.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
