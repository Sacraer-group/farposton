from datetime import date
import sqlite3

import pytest

from tools.extend_venue_history import extend
from web.places_history import create_history


def test_extension_preserves_the_whole_original_history(tmp_path):
    places = {"a": {"id": "a", "categories": [], "rubrics": [], "schedule": {"is_24x7": True}}}
    source, output = tmp_path / "original.sqlite", tmp_path / "extended.sqlite"
    create_history(source, places, date(2026, 10, 13), date(2026, 11, 12))
    result = extend(source, output, date(2026, 9, 13), places)
    assert result["original_rows"] == result["identical_original_rows"] == 31
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 31
    with pytest.raises(FileExistsError):
        extend(source, output, date(2026, 9, 13), places)


def test_extension_rejects_changed_catalogue(tmp_path):
    source = tmp_path / "original.sqlite"
    create_history(source, {"a": {"id": "a"}}, date(2026, 10, 13), date(2026, 11, 12))
    with pytest.raises(ValueError, match="Исходный месяц изменился"):
        extend(source, tmp_path / "extended.sqlite", date(2026, 9, 13), {"b": {"id": "b"}})
