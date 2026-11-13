"""Append-only forecast-release storage.

The legacy forecast log is keyed only by target date, which overwrites D+2,
D+3 and D+4 issues of the same target.  A release is instead immutable and
contains every target file plus one provenance manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

import pandas as pd

from prognoz.assortment import (filter_frame,
                                provenance as assortment_provenance)

_ISSUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
HORIZON_LAGS = {2: 3, 3: 4, 4: 5}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False) as stream:
        tmp = Path(stream.name)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def write_release(root: Path, issue_id: str,
                  forecasts: Mapping[date, pd.DataFrame], provenance: dict) -> Path:
    """Write one immutable issue and return its directory.

    No existing issue is amended.  The temporary directory is renamed only
    after all parquet files and the manifest have been written successfully.
    """
    if not _ISSUE_RE.fullmatch(issue_id):
        raise ValueError("issue_id must be a safe 3–128 character identifier")
    if not forecasts:
        raise ValueError("A release must contain at least one target")
    root = Path(root)
    target = root / issue_id
    if target.exists():
        raise FileExistsError(f"Release already exists: {target}")
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{issue_id}.tmp"
    if tmp.exists():
        raise FileExistsError(f"Incomplete release directory exists: {tmp}")
    log_dir = tmp / "forecast_log"
    log_dir.mkdir(parents=True)
    files = []
    try:
        for target_date, frame in sorted(forecasts.items()):
            if not isinstance(target_date, date):
                raise TypeError("Forecast keys must be datetime.date")
            path = log_dir / f"{target_date.isoformat()}.parquet"
            frame.to_parquet(path, index=False)
            files.append({"target_date": target_date.isoformat(),
                          "path": str(path.relative_to(tmp)), "rows": int(len(frame)),
                          "bytes": path.stat().st_size, "sha256": _sha256(path)})
        manifest = {"issue_id": issue_id, "files": files, "provenance": provenance}
        (tmp / "provenance.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target


def _one_value(frame: pd.DataFrame, column: str):
    if column not in frame.columns:
        raise ValueError(f"Forecast misses required metadata column {column}")
    values = frame[column].dropna().unique().tolist()
    if len(values) != 1:
        raise ValueError(f"Forecast column {column} must have one value, got {values}")
    return values[0]


def _optional_int(frame: pd.DataFrame, column: str, default: int) -> int:
    if column in frame.columns and frame[column].notna().any():
        return int(_one_value(frame, column))
    return int(default)


def _optional_str(frame: pd.DataFrame, column: str) -> str | None:
    if column in frame.columns and frame[column].notna().any():
        return str(_one_value(frame, column))
    return None


def configured_points() -> set[str]:
    """Точки, которые СЧИТАЮТСЯ живыми по конфигу: роли market/market_hub в
    points_map.csv (PROGNOZ_CONFIG_DIR, иначе analysis/config репозитория).
    Файла нет — пустое множество (проверка по конфигу тогда не применяется)."""
    cfg_dir = os.environ.get("PROGNOZ_CONFIG_DIR", "").strip()
    candidates = [Path(cfg_dir)] if cfg_dir else []
    candidates.append(Path(__file__).resolve().parents[3] / "analysis" / "config")
    for root in candidates:
        path = root / "points_map.csv"
        if path.is_file():
            frame = pd.read_csv(path)
            live = frame[frame["role"].astype(str).isin(["market", "market_hub"])]
            return {str(k) for k in live["point_key"].astype(str)}
    return set()


def previous_release_points(release_root: Path) -> set[str]:
    """Точки последнего опубликованного выпуска — эталон «ничего не пропало».
    Нет релизов (первый запуск, чистый стенд) — пустое множество."""
    root = Path(release_root)
    if not root.is_dir():
        return set()
    for path in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        manifest = path / "provenance.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        keys = (data.get("provenance") or {}).get("point_keys") or []
        if keys:
            return {str(k) for k in keys}
    return set()


def archive_issue(output_root: Path, release_root: Path, issue_date: date, *,
                  engine: str = "stacked_guard",
                  issue_id: str | None = None,
                  expected_manifest_sha256: str | None = None) -> Path:
    """Validate and archive one complete D+2/D+3/D+4 nightly issue."""
    if engine not in {"stacked_guard", "baseline", "auto"}:
        raise ValueError("Release engine must be stacked_guard, baseline or auto")
    output_root = Path(output_root)
    forecasts: dict[date, pd.DataFrame] = {}
    target_meta: list[dict] = []
    engines_by_target: dict[str, str] = {}
    manifest_hashes: set[str] = set()
    bundle_lags: set[int] = set()
    cutoffs: set[str] = set()
    versions: set[str] = set()
    point_sets: set[tuple[str, ...]] = set()
    for horizon, lag in HORIZON_LAGS.items():
        target_date = issue_date + timedelta(days=horizon)
        path = output_root / "forecast_log" / f"{target_date.isoformat()}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"No forecast for D+{horizon}: {path}")
        frame = pd.read_parquet(path)
        if {"_point_key", "_sku_key"}.issubset(frame.columns):
            scoped = frame.copy()
            scoped["target_date"] = target_date
            scoped = filter_frame(
                scoped, "factory_order", point_col="_point_key",
                sku_col="_sku_key", date_col="target_date")
            if len(scoped) != len(frame):
                raise ValueError(
                    f"D+{horizon} contains {len(frame) - len(scoped)} "
                    "non-factory assortment rows")
        if frame.empty:
            raise ValueError(f"Forecast for D+{horizon} is empty")
        actual_target = pd.Timestamp(_one_value(frame, "target_date")).date()
        actual_issue = pd.Timestamp(_one_value(frame, "_issue_date")).date()
        actual_horizon = int(_one_value(frame, "_horizon_days"))
        actual_lag = int(_one_value(frame, "_feature_lag"))
        actual_engines = set(frame["engine"].dropna().astype(str))
        if actual_target != target_date or actual_issue != issue_date:
            raise ValueError(f"D+{horizon} target/issue metadata mismatch")
        if actual_horizon != horizon or actual_lag != lag:
            raise ValueError(f"D+{horizon} must use lag={lag}")
        if len(actual_engines) != 1:
            raise ValueError(
                f"D+{horizon} mixes engines inside one file: {sorted(actual_engines)}")
        actual_engine = next(iter(actual_engines))
        if actual_engine not in {"stacked_guard", "baseline"}:
            raise ValueError(f"D+{horizon} has unknown engine {actual_engine}")
        if engine != "auto" and actual_engine != engine:
            raise ValueError(
                f"D+{horizon} requested {engine}, got {sorted(actual_engines)}")
        # auto (17.09): каждый горизонт — своим движком (ML, либо резервная формула
        # по отказу сторожа именно на нём); строгие проверки модели — только для
        # ML-горизонтов. Раньше один отказавший горизонт валил весь выпуск в формулу.
        strict_model = actual_engine == "stacked_guard"
        engines_by_target[target_date.isoformat()] = actual_engine
        versions.add(str(_one_value(frame, "version")))
        if "_point_key" not in frame.columns:
            raise ValueError(f"D+{horizon} has no point keys for RBAC")
        point_sets.add(tuple(sorted(frame["_point_key"].dropna().astype(str).unique())))
        if strict_model:
            variants = set(frame.get(
                "_model_variant", pd.Series(dtype=str)).dropna().astype(str))
            if variants != {"stacked_consensus"}:
                raise ValueError(
                    f"D+{horizon} is not a pure stacked consensus: {sorted(variants)}")
            manifest_hash = str(_one_value(frame, "_bundle_manifest_sha256"))
            if not re.fullmatch(r"[0-9a-f]{64}", manifest_hash):
                raise ValueError(f"D+{horizon} has no valid pinned bundle hash")
            manifest_hashes.add(manifest_hash)
            bundle_lags.add(int(_one_value(frame, "_bundle_lag")))
            cutoff = date.fromisoformat(str(_one_value(frame, "model_cutoff")))
            information_day = target_date - timedelta(days=lag)
            if cutoff > information_day:
                raise ValueError(f"D+{horizon} model cutoff looks into the future")
            if (information_day - cutoff).days > 14:
                raise ValueError(f"D+{horizon} model is older than 14 days")
            cutoffs.add(cutoff.isoformat())
        forecasts[target_date] = frame
        target_meta.append({"target_date": target_date.isoformat(),
                            "horizon_days": horizon, "feature_lag": lag,
                            "rows": int(len(frame)), "engine": actual_engine,
                            "effective_lag": _optional_int(frame, "_effective_lag", lag),
                            "data_as_of": _optional_str(frame, "_data_as_of")})
    ml_targets = [t for t, e in engines_by_target.items() if e == "stacked_guard"]
    fallback_targets = [t for t, e in engines_by_target.items() if e == "baseline"]
    any_model = bool(ml_targets)
    if any_model and (len(manifest_hashes) != 1 or bundle_lags != {3}):
        raise ValueError("The ML horizons must reuse one pinned lag_3 bundle")
    if len(versions) != 1 or len(point_sets) != 1:
        raise ValueError("The three horizons differ in code version or point scope")
    points = next(iter(point_sets))
    # Точки выпуска: чужих быть не должно, а пропасть не должна ни одна из тех, что
    # были в прошлом выпуске. Новая точка появляется в конфиге раньше, чем в данных
    # (справочник приезжает со снимком базы отдельным каналом) — 18.09 жёсткое
    # «ровно N» из-за этого оставило клиента вовсе без выпуска. Такой разрыв —
    # строка points_pending в провенансе, а не отказ публиковать заявку.
    configured = configured_points()
    if not points:
        raise ValueError("Release contains no points")
    # Перекос в обе стороны — обычное дело: конфиг и справочник точек в базе живут
    # разными каналами, и dim_point не пересобирает ни один ночной цикл. Ни один из
    # перекосов не повод оставить сеть без заявки: расхождения идут строкой в
    # провенанс и тревогой в вывод, отказ — только если общего с конфигом нет вовсе.
    unknown = sorted(set(points) - configured) if configured else []
    pending = sorted(configured - set(points)) if configured else []
    if unknown:
        print("ТРЕВОГА: в выпуске точки, которых нет в points_map "
              f"(конфиг отстал от справочника базы): {unknown}", file=sys.stderr)
    if configured and not (set(points) & configured):
        raise ValueError(
            f"Release has no points from points_map at all: {sorted(points)}")
    # Пропажа точки — отказ, но только если она всё ещё считается живой в конфиге:
    # закрытую точку убирают из points_map, и тогда её уход из выпуска законен.
    lost = sorted((previous_release_points(release_root) - set(points))
                  & (configured if configured else set()))
    if lost:
        raise ValueError(f"Release lost points present in the previous issue: {lost}")
    actual_manifest = next(iter(manifest_hashes)) if any_model else None
    if (any_model and expected_manifest_sha256
            and actual_manifest != expected_manifest_sha256.strip().lower()):
        raise ValueError("Release bundle hash differs from the scheduler pin")
    overall = ("stacked_guard" if not fallback_targets
               else "baseline" if not ml_targets else "mixed")
    model_block: dict = {}
    if any_model:
        model_block = {"bundle_lag": 3,
                       "bundle_manifest_sha256": actual_manifest,
                       "model_cutoffs": sorted(cutoffs),
                       "runtime_adapter": "latest_complete_day_to_frozen_lag3",
                       "accuracy_status": "D+3/D+4 require prospective scoring"}
    if fallback_targets:
        model_block.update({
            "fallback": True,
            "method": "baseline",
            "fallback_targets": fallback_targets,
            "reason": ("model release failed; operational order preserved"
                       if not ml_targets else
                       "ML guard rejected some horizons; those carry the formula"),
        })
    provenance = {
        "issue_date": issue_date.isoformat(),
        "engine": overall,
        "engines": engines_by_target,
        "version": next(iter(versions)),
        "point_keys": list(points),
        "points_pending": pending,      # есть в конфиге, но данных ещё нет
        "points_unknown": unknown,      # есть в данных, но не в конфиге
        "targets": target_meta,
        "model": model_block,
        "assortment": assortment_provenance(),
    }
    return write_release(
        release_root, issue_id or issue_date.isoformat(), forecasts, provenance)


def promote_release(release_path: Path, flat_output_root: Path, *,
                    xlsx_source_root: Path | None = None) -> None:
    """Refresh each legacy flat file atomically after a complete archive.

    The file set itself is not the canonical atomic boundary: the web reads the
    immutable latest release first.  Flat parquet/XLSX files remain for backwards
    compatibility and are changed only after all three horizons pass validation.
    """
    release_path = Path(release_path)
    flat_output_root = Path(flat_output_root)
    sources = sorted((release_path / "forecast_log").glob("*.parquet"))
    if not sources:
        raise FileNotFoundError(f"Release has no parquet files: {release_path}")
    xlsx_sources: dict[str, Path] = {}
    if xlsx_source_root is not None:
        for source in sources:
            xlsx = Path(xlsx_source_root) / f"Заявка_{source.stem}.xlsx"
            if not xlsx.is_file():
                raise FileNotFoundError(f"No staged Excel for {source.stem}: {xlsx}")
            xlsx_sources[source.stem] = xlsx
    for source in sources:
        _atomic_copy(source, flat_output_root / "forecast_log" / source.name)
        if source.stem in xlsx_sources:
            xlsx = xlsx_sources[source.stem]
            _atomic_copy(xlsx, flat_output_root / xlsx.name)


def main(argv: list[str] | None = None) -> int:
    project = Path(__file__).resolve().parents[3]
    default_output = project / "analysis" / "output"
    parser = argparse.ArgumentParser(
        description="Archive an immutable D+2/D+3/D+4 forecast issue")
    parser.add_argument("--issue-date", required=True, type=date.fromisoformat)
    parser.add_argument("--output-root", type=Path, default=default_output)
    parser.add_argument("--release-root", type=Path,
                        default=default_output / "releases")
    parser.add_argument("--engine", default="stacked_guard")
    parser.add_argument("--issue-id")
    parser.add_argument("--publish-flat-root", type=Path)
    parser.add_argument("--xlsx-source-root", type=Path)
    args = parser.parse_args(argv)
    try:
        path = archive_issue(
            args.output_root, args.release_root, args.issue_date,
            engine=args.engine, issue_id=args.issue_id,
            expected_manifest_sha256=os.environ.get(
                "PROGNOZ_STACKED_GUARD_MANIFEST_SHA256"))
        if args.publish_flat_root is not None:
            promote_release(
                path, args.publish_flat_root,
                xlsx_source_root=args.xlsx_source_root or args.output_root)
    except Exception as exc:  # noqa: BLE001 - scheduler receives a fail-closed code
        print(f"release archive failed: {type(exc).__name__}: {exc}")
        return 2
    print(f"immutable release: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
