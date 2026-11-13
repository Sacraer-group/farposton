import io
from urllib.error import URLError

from tools import check_2gis
from tools.check_2gis import summarise


def test_summary_distinguishes_false_missing_and_actual_congestion():
    result = summarise({"meta": {"code": 200}, "result": {"items": [
        {"name": "A", "congestion": 0, "has_dynamic_congestion": False},
        {"name": "B", "congestion": {"10": {"Fri": [18]}}, "has_dynamic_congestion": True},
        {"name": "C"},
    ]}})
    assert result["returned"] == 3
    assert result["congestion_non_null"] == 2
    assert result["dynamic_true"] == result["dynamic_false"] == result["dynamic_missing_or_null"] == 1
    assert result["observed_bucket_labels"] == ["10"]
    assert result["observed_day_codes"] == ["Fri"]


def test_api_error_is_not_confused_with_no_coverage():
    result = summarise({"meta": {"code": 403, "error": {"type": "forbidden"}}})
    assert result == {"ok": False, "api_code": 403, "error_type": "forbidden"}


def test_request_reports_api_error_without_exposing_key(monkeypatch, capsys):
    monkeypatch.setenv("TWOGIS_API_KEY", "test-secret")
    monkeypatch.setattr(check_2gis, "urlopen", lambda *a, **kw:
                        io.BytesIO(b'{"meta":{"code":403,"error":{"type":"forbidden"}}}'))
    assert check_2gis.main() == 1
    output = capsys.readouterr()
    assert '"api_code": 403' in output.out
    assert "test-secret" not in output.out + output.err


def test_network_error_does_not_expose_key(monkeypatch, capsys):
    monkeypatch.setenv("TWOGIS_API_KEY", "test-secret")
    def fail(*args, **kwargs):
        raise URLError("URL contains test-secret")
    monkeypatch.setattr(check_2gis, "urlopen", fail)
    assert check_2gis.main() == 1
    output = capsys.readouterr()
    assert "test-secret" not in output.out + output.err
