import argparse
import sys
from pathlib import Path

from flats import db, importer

DEMO_FILES = {
    "real": Path(__file__).with_name("demo") / "metrapi_vladivostok.json",
    "invented": Path(__file__).with_name("demo") / "offers.json",
}


def _import(path: Path) -> int:
    conn = db.connect()
    db.init_db(conn)
    try:
        run_id = importer.run_import(conn, importer.load_json(path))
    except Exception as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    fetched = conn.execute("SELECT fetched FROM import_runs WHERE id = ?", (run_id,)).fetchone()[0]
    print(f"imported {fetched} offers, run #{run_id}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="flats")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the SQLite schema")
    p = sub.add_parser("import-json", help="load normalized offers from a JSON list")
    p.add_argument("path", type=Path)
    p = sub.add_parser("demo", help="load the bundled dataset")
    p.add_argument("--set", choices=sorted(DEMO_FILES), default="real")
    args = parser.parse_args(argv)

    if args.command == "init-db":
        db.init_db(db.connect())
        print("database ready")
        return 0
    if args.command == "import-json":
        return _import(args.path)
    return _import(DEMO_FILES[args.set])


if __name__ == "__main__":
    sys.exit(main())
