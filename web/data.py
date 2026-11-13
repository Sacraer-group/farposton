"""Слой данных веб-сервиса прогноза: снимок DuckDB + parquet-выходы дневного прогона.

Три правила, из-за которых этот модуль вообще существует:
1. Веб читает НЕ рабочую prognoz.duckdb (её держит дневной прогон), а отдельный
   снимок prognoz_web.duckdb — короткоживущим read-only подключением.
2. Parquet-файлы прогон может переписывать прямо в момент чтения — читаем «стабильно»
   (по образцу hours/web/incoming.py::snapshot): сверяем (mtime, size) до и после.
3. Снимка может ещё не быть (каркас поднят раньше данных) — проверка ленивая,
   при импорте модуля файлы не нужны.
"""
from __future__ import annotations

from . import paths  # noqa: F401  — кладёт prognoz/src в sys.path ДО импорта ядра

import time
import os
import json
import hashlib
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path

import duckdb
import pandas as pd

from .config import ROOT, db_snapshot_from_env

# Выходы дневного прогона (прогнозы, свод точности и т.п.). На сервере клиента
# они находятся на диске D:, отдельно от кода и виртуального окружения.


def output_root() -> Path:
    """Корень выходов прогона: WEB_OUTPUT_ROOT → APP_OUTPUT_ROOT → analysis/output.

    Единственное место, где этот выбор делается. Ночной цикл клиента пишет всё
    (в том числе утренние сводки ai_reports/) по APP_OUTPUT_ROOT — модули,
    которым нужен подкаталог, берут его отсюда, а не через свой ROOT/analysis/…
    """
    raw = (os.getenv("WEB_OUTPUT_ROOT", "").strip()
           or os.getenv("APP_OUTPUT_ROOT", "").strip())
    return Path(raw).resolve() if raw else ROOT / "analysis" / "output"


OUTPUT = output_root()


def ai_reports_dir() -> Path:
    """Папка утренних сводок — <OUTPUT>/ai_reports, та же, куда их кладёт
    src/prognoz/report/morning.py. Функция, а не константа: читается из
    окружения при вызове (до 08.09 путь был зашит в ROOT/analysis/output, и на
    сервере клиента вкладка «Сводки» была пустой)."""
    return output_root() / "ai_reports"


def facts_path() -> Path:
    """analysis/output/facts.json (реестр фактов) — рядом со сводками."""
    return output_root() / "facts.json"


_forecast_root = os.getenv('WEB_FORECAST_LOG_DIR', '').strip()
FORECAST_LOG = (Path(_forecast_root).resolve() if _forecast_root else OUTPUT / "forecast_log")
_release_root = os.getenv("WEB_RELEASE_ROOT", "").strip()
RELEASE_ROOT = (Path(_release_root).resolve()
                if _release_root else OUTPUT / "releases")


# --- снимок DuckDB ------------------------------------------------------------

@contextmanager
def connect_snapshot():
    """Короткоживущее read-only подключение к снимку БД: открыли — прочитали — закрыли.

    Дневной прогон раз в день подменяет файл снимка, поэтому соединение не живёт
    дольше одного запроса. 3 попытки с паузой 0,5 с — на случай чтения в момент подмены.
    """
    db_path = db_snapshot_from_env()
    last_exc: Exception | None = None
    for _ in range(3):
        try:
            con = duckdb.connect(str(db_path), read_only=True,
                                 config={"memory_limit": "500MB", "threads": 1})
        except (OSError, duckdb.IOException) as exc:
            last_exc = exc
            time.sleep(0.5)
            continue
        try:
            yield con
            return
        finally:
            con.close()
    raise RuntimeError(f"Снимок БД недоступен: {db_path} ({last_exc})")


# --- стабильное чтение parquet ------------------------------------------------

def read_parquet_stable(path: Path | str, attempts: int = 3,
                        settle_seconds: float = 0.2) -> pd.DataFrame:
    """Чтение parquet, который прогон может переписывать прямо сейчас.

    (mtime, size) снимаются дважды с паузой; совпали — читаем; после чтения
    сверяем ещё раз, что источник не менялся. Не устоялся за attempts — ошибка.
    """
    p = Path(path)
    last_err = "файл постоянно меняется"
    for _ in range(attempts):
        st1 = p.stat()
        time.sleep(settle_seconds)
        st2 = p.stat()
        if (st1.st_size, st1.st_mtime) != (st2.st_size, st2.st_mtime):
            last_err = "файл обновляется прогоном прямо сейчас"
            continue
        try:
            df = pd.read_parquet(p)
        except Exception:
            last_err = "файл не прочитался как parquet"
            time.sleep(settle_seconds)
            continue
        st3 = p.stat()
        if (st2.st_size, st2.st_mtime) != (st3.st_size, st3.st_mtime):
            last_err = "файл изменился во время чтения"
            continue
        return df
    raise RuntimeError(f"Не удалось стабильно прочитать {p.name}: {last_err}")


# --- кэш с TTL и инвалидацией по mtime снимка ---------------------------------

def ttl_cache(seconds: int = 300):
    """Кэш результата функции на `seconds`, ключ включает mtime файла снимка БД:
    прогон подменил снимок — кэш протухает сам, без ручного сброса."""
    def deco(fn):
        cache: dict[tuple, tuple[float, object]] = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                db_mtime = db_snapshot_from_env().stat().st_mtime
            except OSError:
                db_mtime = 0.0
            key = (fn.__name__, args, tuple(sorted(kwargs.items())), db_mtime)
            now = time.monotonic()
            hit = cache.get(key)
            if hit is not None and now - hit[0] < seconds:
                return hit[1]
            value = fn(*args, **kwargs)
            cache[key] = (now, value)
            if len(cache) > 64:   # страховка от распухания (старые mtime копятся)
                oldest = min(cache, key=lambda k: cache[k][0])
                cache.pop(oldest, None)
            return value

        wrapper.cache_clear = cache.clear
        return wrapper
    return deco


# --- свежесть снимка БД -------------------------------------------------------
# Единственная точка правды про «когда обновлялся снимок»: её зовут и /health,
# и /api/meta в web/app.py. Время — СЕРВЕРНОЕ (как в mtime); сдвиг на UTC+10
# делает app.py на выдаче, здесь ничего не сдвигаем.

def snapshot_mtime() -> datetime | None:
    """mtime файла снимка БД серверным временем; снимка ещё нет — None."""
    try:
        return datetime.fromtimestamp(db_snapshot_from_env().stat().st_mtime)
    except OSError:
        return None


def snapshot_age_hours() -> float | None:
    """Возраст снимка БД в часах (1 знак); снимка нет — None."""
    mt = snapshot_mtime()
    if mt is None:
        return None
    return round((datetime.now() - mt).total_seconds() / 3600, 1)


# --- готовые выборки ----------------------------------------------------------

@ttl_cache(seconds=300)
def freshness() -> dict:
    """Свежесть продаж по точкам из снимка БД.

    Возвращает {"points": [имена точек], "max_sale_date": "YYYY-MM-DD" | None,
    "freshness": [{"point": ..., "last_sale": "YYYY-MM-DD"}, ...]}.

    Запрос — тот же костяк, что у weekday_reference в src/prognoz/predict/daily.py
    (канонизация merged-точек + роли market/market_hub), только без дня недели.
    """
    from prognoz.scope import canon_cte  # noqa: PLC0415 — ядро доступно после paths

    with connect_snapshot() as con:
        rows = con.execute(f"""
            WITH {canon_cte()},
            nm AS (SELECT canon_name, point_key AS pt FROM mart.dim_point
                   WHERE role IN ('market', 'market_hub'))
            SELECT nm.canon_name AS point, max(f.sale_date) AS last_sale
            FROM mart.fact_sales_daily f
            JOIN c ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            GROUP BY 1 ORDER BY 1
        """).fetchall()

    fresh = [{"point": point, "last_sale": str(last_sale)[:10]}
             for point, last_sale in rows if last_sale is not None]
    max_sale = max((f["last_sale"] for f in fresh), default=None)
    return {
        "points": [f["point"] for f in fresh],
        "max_sale_date": max_sale,
        "freshness": fresh,
    }


def latest_release_log() -> Path | None:
    """Return the newest complete immutable issue, or None for legacy installs."""
    if not RELEASE_ROOT.is_dir():
        return None
    candidates = sorted(
        (path for path in RELEASE_ROOT.iterdir()
         if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.name, reverse=True,
    )
    for release in candidates:
        try:
            manifest = json.loads(
                (release / "provenance.json").read_text(encoding="utf-8"))
            files = manifest["files"]
            if manifest.get("issue_id") != release.name or len(files) != 3:
                continue
            provenance = manifest.get("provenance", {})
            horizons = {item.get("horizon_days")
                        for item in provenance.get("targets", [])}
            if (provenance.get("engine") not in {"stacked_guard", "baseline"}
                    or horizons != {2, 3, 4}):
                continue
            log_dir = release / "forecast_log"
            expected = {str(item["target_date"]) for item in files}
            actual = {path.stem for path in log_dir.glob("*.parquet")}
            if actual != expected:
                continue
            intact = True
            for item in files:
                path = log_dir / f"{item['target_date']}.parquet"
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.stat().st_size != item.get("bytes") or digest != item.get("sha256"):
                    intact = False
                    break
            if not intact:
                continue
            return log_dir
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def forecast_path(target_date: str) -> Path:
    """Resolve an order from the latest complete issue, then legacy history."""
    active = latest_release_log()
    if active is not None:
        candidate = active / f"{target_date}.parquet"
        if candidate.is_file():
            return candidate
    return FORECAST_LOG / f"{target_date}.parquet"


def forecast_dates() -> list[str]:
    """Отсортированные даты, на которые считались прогнозы, — имена файлов
    analysis/output/forecast_log/*.parquet (файл = дата, содержимое не читаем)."""
    dates = ({p.stem for p in FORECAST_LOG.glob("*.parquet")}
             if FORECAST_LOG.is_dir() else set())
    active = latest_release_log()
    if active is not None:
        dates.update(path.stem for path in active.glob("*.parquet"))
    return sorted(dates)
