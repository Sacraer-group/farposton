"""Real browser acceptance. Install .[dev,browser] and Playwright Chromium to run."""

from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest

pw = pytest.importorskip("playwright.sync_api", reason="Install the optional browser extra for browser checks")

from flats import db
from flats.cli import DEMO_FILES
from flats.house_import import import_house_passports, load_house_bundle
from flats.importer import load_json, run_import
from flats.web import create_app


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def site(tmp_path, monkeypatch):
    from werkzeug.serving import make_server

    monkeypatch.setenv("FLATS_PUBLIC_DEMO", "1")
    monkeypatch.setenv("FLATS_SECRET_KEY", "browser-acceptance-only-not-a-production-secret")
    monkeypatch.setenv("FLATS_SECURE_COOKIE", "0")
    for name in ("FLATS_LLM_BASE_URL", "FLATS_LLM_MODEL", "FLATS_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "browser.db"
    conn = db.connect(path)
    db.init_db(conn)
    run_import(conn, load_json(DEMO_FILES["real"]))
    archive = Path(__file__).resolve().parents[1] / "research/house-passports/2026-11-14/farposton-house-data-2026-11-14.zip"
    import_house_passports(conn, load_house_bundle(archive))
    conn.close()
    app = create_app(path)
    app.testing = True
    server = make_server("127.0.0.1", 0, app, threaded=True)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def context_for(browser, width):
    context = browser.new_context(viewport={"width": width, "height": 1000}, locale="ru-RU")
    # Browser checks exercise our real HTML/API; remote photos and tile servers
    # must not make a deterministic acceptance check depend on public networks.
    context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname == "127.0.0.1"
                  else route.abort())
    return context


def check_layout(page, name):
    page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Horizontal overflow"
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_text_to_viewing_flow_and_browser_local_favorites(browser, site, width):
    context = context_for(browser, width)
    page = context.new_page()
    errors, legacy_writes = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: legacy_writes.append(request.url)
            if urlsplit(request.url).path in ("/api/decide", "/api/undo") else None)
    page.goto(site, wait_until="networkidle")
    check_layout(page, f"catalog-{width}")
    task = "Ищу однокомнатную до 50 тыс в месяц. Работаю из дома."
    page.locator("#search-task").fill(task)
    page.get_by_role("button", name="Разобрать задачу").click()
    pw.expect(page.locator("#request-proposal")).to_be_visible()
    pw.expect(page.locator("#request-proposal")).to_be_focused()
    assert page.evaluate("""() => {
        const proposal = document.getElementById('request-proposal').getBoundingClientRect();
        const header = document.querySelector('.site-header').getBoundingClientRect();
        return proposal.top >= header.bottom - 1 && proposal.top < window.innerHeight - 40;
    }""")
    pw.expect(page.locator('#advisor-form input[name="monthly_max"]')).to_have_value("")
    pw.expect(page.locator("#recommendations > article")).to_have_count(0)
    check_layout(page, f"request-{width}")
    page.locator("#apply-request").click()
    pw.expect(page.locator('#advisor-form input[name="monthly_max"]')).to_have_value("50000")
    pw.expect(page.locator('#advisor-form input[name="rooms"][value="1"]')).to_be_checked()
    with page.expect_response(lambda response: urlsplit(response.url).path == "/api/advisor") as answer:
        page.locator("#ask-advisor").click()
    assert answer.value.status == 200
    data = answer.value.json()
    assert data["mode"] == "retrieval" and 1 <= len(data["recommendations"]) <= 3
    pw.expect(page.locator("#recommendations > article")).to_have_count(len(data["recommendations"]))
    pw.expect(page.locator("#viewing-brief")).to_be_visible()
    pw.expect(page.locator("#applied-conditions")).to_contain_text("50")
    check_layout(page, f"advisor-{width}")

    house_url = page.locator("#recommendations [data-house]").first.get_attribute("href")
    assert "/house/" in house_url
    house = context.new_page()
    assert house.goto(house_url).status == 200
    pw.expect(house.locator("main")).to_contain_text("Источники")
    check_layout(house, f"house-{width}")
    house.close()

    comparison_url = page.locator("#compare-candidates").get_attribute("href")
    comparison = context.new_page()
    assert comparison.goto(site + comparison_url).status == 200
    pw.expect(comparison.locator("main")).to_contain_text("На въезд")
    pw.expect(comparison.locator("tbody tr").filter(has_text="Сопоставление дома")).to_contain_text("Адрес сопоставлен")
    pw.expect(comparison.get_by_role("rowheader", name="Архив публикаций об отключениях")).to_be_visible()
    check_layout(comparison, f"comparison-{width}")
    comparison.close()
    assert page.evaluate("window.FlatsDecisions.current().length") == 0

    save = page.locator("#recommendations [data-save-candidate]").first
    save.click()
    pw.expect(save).to_be_disabled()
    saved = context.new_page()
    saved.goto(site + "/favorites")
    pw.expect(saved.locator("#favorites-count")).to_have_text("1")
    saved.reload()
    pw.expect(saved.locator("#favorites-count")).to_have_text("1")
    check_layout(saved, f"favorites-{width}")

    saved_advisor = context.new_page()
    saved_advisor.goto(site + "/advisor?scope=saved")
    saved_advisor.locator("#ask-advisor").click()
    pw.expect(saved_advisor.locator("#recommendations > article")).to_have_count(1)
    pw.expect(saved_advisor.locator("#viewing-brief")).to_be_visible()

    second_context = context_for(browser, width)
    fresh = second_context.new_page()
    fresh.goto(site + "/favorites")
    pw.expect(fresh.locator("#favorites-empty")).to_be_visible()
    pw.expect(fresh.locator("#favorites-content")).to_be_hidden()
    second_context.close()

    # Removing a favorite means skipping it; neither an old saved-scope plan nor
    # the search recommendation may keep presenting it after another tab changes it.
    saved.locator('[data-saved-offer]:visible button[value="skip"]').click()
    pw.expect(saved.locator("#favorites-empty")).to_be_visible()
    pw.expect(page.locator("#recommendations > article")).to_have_count(0)
    pw.expect(page.locator("#viewing-brief")).to_be_hidden()
    pw.expect(saved_advisor.locator("#recommendations > article")).to_have_count(0)
    pw.expect(saved_advisor.locator("#viewing-brief")).to_be_hidden()
    saved_advisor.close()
    saved.close()
    assert not legacy_writes and not errors
    context.close()


def test_manual_form_empty_recovery_draft_and_request_errors(browser, site):
    context = context_for(browser, 390)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site + "/advisor?scope=saved")
    page.locator("#ask-advisor").click()
    pw.expect(page.locator("#advisor-status")).to_contain_text("нет предложений")
    pw.expect(page.locator("#viewing-brief")).to_be_hidden()

    page.goto(site + "/advisor")
    page.locator('[name="monthly_max"]').fill("1")
    page.locator("#ask-advisor").click()
    pw.expect(page.locator("#advisor-status")).to_contain_text("нет предложений")
    pw.expect(page.locator(".empty-recovery")).to_be_visible()
    page.locator("#clear-scenario").click()
    page.locator('[name="monthly_max"]').fill("60000")
    page.reload()
    pw.expect(page.locator('[name="monthly_max"]')).to_have_value("60000")

    page.route("**/api/advisor", lambda route: route.fulfill(status=503, content_type="application/json",
               body='{"error":"Временная ошибка проверки. Повторите запрос."}'), times=1)
    page.locator("#ask-advisor").click()
    pw.expect(page.locator("#advisor-status")).to_contain_text("Временная ошибка проверки")
    pw.expect(page.locator('[name="monthly_max"]')).to_have_value("60000")
    page.locator("#ask-advisor").click()
    pw.expect(page.locator("#viewing-brief")).to_be_visible()

    page.locator("#requirements-text").fill("До 30 тыс в месяц")
    pw.expect(page.locator("#viewing-brief")).to_be_hidden()
    page.locator("#ask-advisor").click()
    pw.expect(page.locator("#request-proposal")).to_be_visible()
    pw.expect(page.locator("#request-proposal")).to_be_focused()
    assert page.evaluate("""() => {
        const proposal = document.getElementById('request-proposal').getBoundingClientRect();
        const header = document.querySelector('.site-header').getBoundingClientRect();
        return proposal.top >= header.bottom - 1 && proposal.top < window.innerHeight - 40;
    }""")
    pw.expect(page.locator('[name="monthly_max"]')).to_have_value("60000")
    pw.expect(page.locator("#recommendations > article")).to_have_count(0)
    assert not errors
    context.close()
