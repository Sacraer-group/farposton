from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_order_page_uses_explicit_atomic_bulk_save():
    html = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="saveEditsBtn"' in html
    assert "Сохранить правки" in html
    assert 'id="editStatus" aria-live="polite"' in html
    assert "const pendingEdits = new Map()" in html
    assert "apiJSON('/api/edits/bulk'" in html
    assert "apiJSON('/api/edits'," not in html
    assert "release_id: saveRelease" in html
    assert "result.count !== edits.length" in html
    assert "beforeunload" in html
    assert 'id="logoutBtn"' in html
    assert "if (savingEdits)" in html
    assert "Сначала завершите и сохраните правки, затем скачивайте Excel" in html
    assert "Сначала завершите и сохраните правки, затем формируйте заявку в 1С" in html


def test_order_rows_expose_revision_and_manager_export_counts_only_visible_edits():
    order_source = (ROOT / "web" / "order.py").read_text(encoding="utf-8")
    export_source = (ROOT / "web" / "export.py").read_text(encoding="utf-8")

    assert '"edit_revision": int(edit_revision)' in order_source
    assert '"release_id": release_id' in order_source
    assert "visible_keys = set(keys)" in export_source
    assert "if key in visible_keys" in export_source


def test_save_rejects_active_invalid_cell_before_blur_or_partial_batch():
    html = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("async function savePendingEdits()")
    end = html.index("$('saveEditsBtn').addEventListener", start)
    save_source = html[start:end]

    validation = save_source.index("!Number.isInteger(value)")
    blur = save_source.index("openInput.blur()")
    post = save_source.index("apiJSON('/api/edits/bulk'")
    assert validation < blur < post
    assert "const openInput = openEditor()" in save_source
    assert "openInput.focus()" in save_source
    assert "openInput.select()" in save_source
    assert "return false;" in save_source[validation:blur]

    edit_start = html.index("const finish = cancel =>")
    edit_end = html.index("inp.addEventListener('keydown'", edit_start)
    finish_source = html[edit_start:edit_end]
    assert "inp.setAttribute('aria-invalid', 'true')" in finish_source
    assert "show(); return;" not in finish_source[
        finish_source.index("!Number.isInteger(val)") :
        finish_source.index("done = true;", finish_source.index("!Number.isInteger(val)"))
    ]
    assert "const hasOpenEditor = () => !!openEditor()" in html
    assert "if (pendingEdits.size || hasOpenEditor())" in html
    date_change = html[html.index("sel.addEventListener('change'") :]
    assert date_change.index("discardPending();") < date_change.index("curDate = nextDate")


def test_legacy_single_edit_endpoint_is_explicitly_gone():
    source = (ROOT / "web" / "routes_order.py").read_text(encoding="utf-8")

    assert '410, "Поштучное сохранение отключено' in source


# ---- 08.09: подписи дат, колонка «Продано за 28 дн.», баннер записи в 1С ------

def test_order_page_labels_horizons_and_sold28_column():
    html = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    order_source = (ROOT / "web" / "order.py").read_text(encoding="utf-8")

    # подписи дат в селекторе: рабочая (завтра), послезавтра, предварительно, архив
    assert "' · рабочая заявка'" in html
    assert "' · на послезавтра'" in html
    assert "' · предварительно'" in html
    assert "' · архив'" in html
    assert "curDate < META.operational_date" in html
    # колонки таблицы
    assert "text: 'Продажи в этот день недели'" in html
    assert "−7 / −14 / −21 / −28 дней: продажи в тот же день недели" in html
    assert "text: 'Продано за 28 дн.'" in html
    assert "всего продано за последние 28 дней" in html
    assert "fmt(r.sold28)" in html
    assert '"sold28": (int(round(_f(r.get("Продано за 28 дн"))))' in order_source
    assert 'if "Продано за 28 дн" in view.columns' in order_source
    # метка движка и баннер после записи в 1С
    assert "'резерв: формула КВП'" in html
    assert "'ML-ядро · ансамбль'" in html
    assert "в списке «Заказы на перемещение», " in html
    assert "'Создано: заказ №' + num + ' в 1С (непроведённый)'" in html
