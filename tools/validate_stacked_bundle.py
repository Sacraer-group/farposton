"""Validate one immutable stacked_guard bundle for the next D+2/D+3/D+4 issue."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from prognoz.predict import stacked_guard_core as core  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--issue-date", required=True, type=date.fromisoformat)
    parser.add_argument("--active-file", type=Path)
    args = parser.parse_args()
    if os.environ.get("PROGNOZ_1C_WRITE") != "0":
        raise RuntimeError("PROGNOZ_1C_WRITE must be exactly 0")
    bundle = args.bundle.resolve()
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = core._manifest_digest(manifest)
    os.environ["PROGNOZ_STACKED_GUARD_BUNDLE"] = str(bundle)
    os.environ["PROGNOZ_STACKED_GUARD_MANIFEST_SHA256"] = digest
    targets = []
    for horizon, lag in ((2, 3), (3, 4), (4, 5)):
        target = args.issue_date + timedelta(days=horizon)
        loaded = core.load_bundle(target=target, cfg=core.GuardCfg(lag=lag))
        targets.append({
            "horizon": horizon,
            "lag": lag,
            "target": target.isoformat(),
            "bundle": str(loaded.root),
        })
    payload = {
        "status": "ok",
        "bundle": str(bundle),
        "manifest_sha256": digest,
        "targets": targets,
    }
    if args.active_file:
        active = args.active_file.resolve()
        active.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".active-bundle-", suffix=".json",
                                         dir=active.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            os.replace(temp_name, active)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
