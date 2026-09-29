# -*- coding: utf-8 -*-
"""API страницы «Заявка»: заявка дня, правки, справочный пересчёт, выгрузка Excel.

Подключается оркестратором: app.include_router(routes_order.router).
Все маршруты — за require_auth (та же зависимость, что у /api/meta в app.py).
"""
from __future__ import annotations

import math
import os
import re
from datetime import date as _date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel, StrictFloat, StrictInt
from starlette.background import BackgroundTask

from web import edits as edits_store
from web import export as export_mod
from web import order as order_mod
from web.auth import client_ip, require_auth
from web.access import principal_from_session, request_point, require_owned_point
from web.config import writer_path as _writer_path  # noqa: F401 — единая точка правды пути

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

_LOCAL_TZ = timezone(timedelta(hours=10))
_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_RELEASE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_BULK_EDITS = 1_000

# Запись заявки в 1С: ВЫКЛЮЧЕНА до письменного «да» 1С-ника. Флаг не в .env
# намеренно — его появление в окружении и есть акт включения.
_PUSH1C_OFF_MSG = "ждёт подключения 1С — согласование с 1С-ником"


def _push1c_enabled() -> bool:
    return os.environ.get("PROGNOZ_1C_WRITE") == "1"


_WRITER_MOD = None
_WRITER_STAMP: tuple[str, int] | None = None      # (путь, mtime_ns) загруженного файла


# Путь к odata/writer.py считает web.config.writer_path (его же проверяет
# профиль client при старте, когда запись в 1С включена); здесь — только
# ленивая загрузка модуля.


def _load_writer():
    """Ленивая загрузка odata/writer.py (вне пакета web) по пути. Модуль перечитывается,
    только когда файл на диске изменился (mtime): 10.09 исправленный писатель неделю
    лежал на диске, а служба слала документы старым кодом из памяти (ТМНФ-001985).
    Без изменений файла повторного exec_module нет — он сбрасывал бы кэш справочника
    (64 МБ jsonl + 500 МБ номенклатуры при новинках — десятки секунд на нажатие).
    Веб обязан подниматься и жить, даже если папки odata на машине нет."""
    global _WRITER_MOD, _WRITER_STAMP
    import importlib.util  # noqa: PLC0415
    path = _writer_path()
    try:
        stamp = (str(path), path.stat().st_mtime_ns)
    except OSError:
        stamp = (str(path), 0)
    if _WRITER_MOD is not None and stamp == _WRITER_STAMP:
        return _WRITER_MOD
    spec = importlib.util.spec_from_file_location("app_1c_writer", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if _WRITER_MOD is not None:
        logger.info("push1c: писатель 1С перечитан с диска ({p})", p=path)
    _WRITER_MOD, _WRITER_STAMP = mod, stamp
    return mod


def _check_date(raw: str) -> str:
    try:
        return _date.fromisoformat(raw.strip()).isoformat()
    except (ValueError, AttributeError):
        raise HTTPException(422, "Дата должна быть в формате ГГГГ-ММ-ДД")


def _business_today() -> _date:
    """Дата операционного дня Прогноз заказов (UTC+10, UTC+10)."""
    return datetime.now(_LOCAL_TZ).date()


# Рабочая заявка — на ЗАВТРА по UTC+10: ночной цикл выпускает её в 05:30,
# менеджер в течение дня правит и отправляет в 1С. Окно правок и записи в 1С —
# завтра и послезавтра (D+1 и D+2): оба выпуска уже окончательные. Даты дальше
# (D+3/D+4) — предварительные: ночью перевыпускаются с новым идентификатором
# выпуска (release_id), и правки к ним молча пропадали бы из заявки — ревью
# 07.09, находка 7. Сегодняшняя и прошедшие даты уже отгружены — правки закрыты.
OPERATIONAL_HORIZON_DAYS = 1
EDIT_WINDOW_DAYS = 2          # последняя редактируемая дата = сегодня + 2


def _operational_date(business_today: _date) -> _date:
    return _date.fromordinal(business_today.toordinal() + OPERATIONAL_HORIZON_DAYS)


def _edit_window(business_today: _date) -> tuple[_date, _date]:
    """[первая, последняя] дата, к которой принимаются правки и запись в 1С."""
    return (_operational_date(business_today),
            _date.fromordinal(business_today.toordinal() + EDIT_WINDOW_DAYS))


def _editable(d: str, business_today: _date) -> bool:
    lo, hi = _edit_window(business_today)
    return lo <= _date.fromisoformat(d) <= hi


_EDITS_CLOSED_MSG = "Заявка на эту дату уже отгружена — правки закрыты"
_EDITS_PRELIMINARY_MSG = ("Правки принимаются к заявкам на завтра и послезавтра: "
                          "дальние даты предварительные и ночью пересчитываются заново")


def _reject_outside_window(d: str, business_today: _date) -> None:
    """422 с человеческой причиной: прошедшее/сегодня — отгружено, дальше — предварительно."""
    if _editable(d, business_today):
        return
    if _date.fromisoformat(d) < _edit_window(business_today)[0]:
        raise HTTPException(422, _EDITS_CLOSED_MSG)
    raise HTTPException(422, _EDITS_PRELIMINARY_MSG)


@router.get("/order")
async def api_order(date: str, request: Request, point: str | None = None):
    """Заявка на дату: {rows: [...], meta: {...}}. point — фильтр по имени точки."""
    d = _check_date(date)
    try:
        principal = principal_from_session(request)
        if principal.is_admin:
            scoped_name = point
        else:
            point_key = request_point(principal)
            own_name = order_mod.point_name_for_key(d, point_key)
            # The browser normally omits ``point`` for a manager.  If somebody
            # injects it into a direct request, fail explicitly instead of
            # silently ignoring the attempted cross-point selection.
            if point is not None and point not in {point_key, own_name}:
                raise HTTPException(403, "Нет доступа к другой точке")
            scoped_name = own_name
        payload = order_mod.get_order(d, point=scoped_name)
        business_today = _business_today()
        payload.setdefault("meta", {})["business_date"] = business_today.isoformat()
        payload["meta"]["operational_date"] = _operational_date(business_today).isoformat()
        payload["meta"]["editable"] = _editable(d, business_today)
    except FileNotFoundError:
        raise HTTPException(404, f"Заявка на {d} не считалась — файла прогноза нет")
    except ValueError as e:
        # точка пользователя не сопоставлена с файлом дня — не 500, а внятный ответ
        raise HTTPException(404, f"В заявке на {d} нет вашей точки ({e})")
    return JSONResponse(payload)


class EditIn(BaseModel):
    """Тело POST /api/edits. qty_user=null — снять правку (вернуться к прогнозу)."""
    target_date: str
    point_key: str
    sku_key: str
    qty_user: float | None = None


@router.post("/edits")
async def api_add_edit(body: EditIn, request: Request):
    """Старый поштучный контракт закрыт: он не умеет атомарность и ревизии."""
    _check_date(body.target_date)
    require_owned_point(principal_from_session(request), body.point_key)
    raise HTTPException(
        410, "Поштучное сохранение отключено — обновите страницу и сохраните весь набор")


class BulkEditItem(BaseModel):
    """Одна изменённая строка; revision — версия, увиденная браузером."""
    point_key: str
    sku_key: str
    qty_user: StrictInt | StrictFloat | None = None
    expected_revision: StrictInt


class BulkEditsIn(BaseModel):
    """Один атомарный набор правок выбранной даты."""
    target_date: str
    release_id: str
    batch_id: str
    edits: list[BulkEditItem]


@router.post("/edits/bulk")
async def api_add_edits_bulk(body: BulkEditsIn, request: Request):
    """Проверяет весь набор, затем сохраняет его одной транзакцией SQLite."""
    d = _check_date(body.target_date)
    release_id = body.release_id.strip()
    if not _RELEASE_ID_RE.fullmatch(release_id):
        raise HTTPException(422, "Некорректный идентификатор выпуска заявки")
    batch_id = body.batch_id.strip()
    if not _BATCH_ID_RE.fullmatch(batch_id):
        raise HTTPException(422, "Некорректный идентификатор сохранения")
    if not 1 <= len(body.edits) <= _MAX_BULK_EDITS:
        raise HTTPException(
            422, f"За один раз можно сохранить от 1 до {_MAX_BULK_EDITS} правок")
    if _date.fromisoformat(d) < _edit_window(_business_today())[0]:
        raise HTTPException(422, _EDITS_CLOSED_MSG)

    principal = principal_from_session(request)
    seen: set[tuple[str, str]] = set()
    entries: list[tuple[str, str, float | None, int]] = []
    for item in body.edits:
        point_key = item.point_key
        sku_key = item.sku_key
        if not point_key.strip() or not sku_key.strip():
            raise HTTPException(422, "У правки отсутствует ключ точки или позиции")
        require_owned_point(principal, point_key)
        key = (point_key, sku_key)
        if key in seen:
            raise HTTPException(422, "Одна строка не может повторяться в наборе правок")
        seen.add(key)
        if item.expected_revision < 0 or item.expected_revision > 9_223_372_036_854_775_807:
            raise HTTPException(422, "Некорректная версия строки заявки")
        qty_user = item.qty_user
        if qty_user is not None and (
                not math.isfinite(qty_user)
                or qty_user < 0 or qty_user > 100_000
                or not float(qty_user).is_integer()):
            raise HTTPException(422, "Правка должна быть целым числом от 0 до 100 000")
        entries.append((point_key, sku_key,
                        float(qty_user) if qty_user is not None else None,
                        int(item.expected_revision)))

    # Окно правок — после проверки принадлежности строк: чужая точка обязана
    # получить 403, а не подсказку про даты. Дальние даты (D+3/D+4) ночью
    # перевыпускаются, и правки к ним пропадали бы из заявки (ревью 07.09).
    _reject_outside_window(d, _business_today())

    try:
        current_release = order_mod.release_fingerprint(d)
        if current_release != release_id:
            raise HTTPException(
                409, "Выпуск заявки обновился — откройте дату заново и повторите правки")
        allowed = order_mod.valid_pairs(d, release_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Заявка на {d} не считалась — правки не приняты")
    except RuntimeError:
        raise HTTPException(
            409, "Выпуск заявки обновляется — откройте дату заново и повторите правки")
    except ValueError:
        logger.exception("bulk edits: выпуск {d} нельзя безопасно сопоставить", d=d)
        raise HTTPException(422, "Этот выпуск не поддерживает безопасные пакетные правки")
    if any((point_key, sku_key) not in allowed
           for point_key, sku_key, _qty, _revision in entries):
        raise HTTPException(422, "Одна или несколько строк отсутствуют в заявке этой даты")

    try:
        if order_mod.release_fingerprint(d) != release_id:
            raise HTTPException(
                409, "Выпуск заявки обновился — откройте дату заново и повторите правки")
        result = edits_store.add_edits_bulk(
            d, release_id, entries, client_ip(request), principal.username, batch_id)
    except edits_store.EditConflictError as exc:
        raise HTTPException(409, str(exc))
    except edits_store.BatchReplayMismatch as exc:
        raise HTTPException(409, str(exc))
    except (FileNotFoundError, RuntimeError):
        raise HTTPException(
            409, "Выпуск заявки обновляется — откройте дату заново и повторите правки")
    return JSONResponse({
        "ok": True, "release_id": release_id, "batch_id": batch_id, **result})


@router.get("/edits")
async def api_edits_history(date: str, request: Request):
    """Полная история правок даты (свежие сверху)."""
    d = _check_date(date)
    principal = principal_from_session(request)
    history = edits_store.get_history(d, request_point(principal))
    if not principal.is_admin:
        history = [
            {key: value for key, value in row.items()
             if key not in {"ip", "actor", "batch_id"}}
            for row in history
        ]
    return JSONResponse({"date": d, "edits": history})


@router.get("/order/export")
async def api_order_export(date: str, request: Request):
    """Excel «Заявка_<дата>_с_правками.xlsx»; временный файл удаляется после отдачи."""
    d = _check_date(date)
    try:
        path = export_mod.build_export(d, request_point(principal_from_session(request)))
    except FileNotFoundError:
        raise HTTPException(404, f"Заявка на {d} не считалась — выгружать нечего")
    except ValueError as e:
        raise HTTPException(404, f"В заявке на {d} нет вашей точки ({e})")
    return FileResponse(
        str(path),
        filename=f"Заявка_{d}_с_правками.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(os.remove, str(path)),
    )


@router.get("/order/reference")
async def api_order_reference(date: str, request: Request, weeks: int = 4):
    """Справочный пересчёт формулой по weeks∈{4,3,2} неделям: {rows, weeks, excluded}."""
    d = _check_date(date)
    if weeks not in (2, 3, 4):
        raise HTTPException(422, "weeks должен быть 2, 3 или 4")
    try:
        payload = order_mod.get_reference(d, weeks, request_point(principal_from_session(request)))
    except FileNotFoundError:
        raise HTTPException(404, f"Заявка на {d} не считалась — пересчитывать нечего")
    except ValueError as e:
        raise HTTPException(404, f"В заявке на {d} нет вашей точки ({e})")
    return JSONResponse(payload)


# ----- запись заявки в 1С (подготовка; сама запись выключена) -----------------

@router.get("/order/push1c/status")
async def api_push1c_status():
    """Статус автозаписи в 1С — для честной надписи на кнопке страницы «Заявка»."""
    if _push1c_enabled():
        return JSONResponse({"enabled": True, "message": "Запись в 1С включена"})
    return JSONResponse({"enabled": False, "message": _PUSH1C_OFF_MSG})


class Push1cIn(BaseModel):
    """Тело POST /api/order/push1c: какую заявку писать."""
    date: str
    point: str


@router.post("/order/push1c")
def api_push1c(body: Push1cIn, request: Request):
    """Запись заявки (дата × точка) в 1С. Обычный def: когда запись включат,
    сетевой вызов writer.push уедет в threadpool и не заблокирует event loop."""
    d = _check_date(body.date)
    # Скоуп по точке — ДО проверки флага: когда запись включат, менеджер не должен
    # уметь выпустить в 1С заявку чужой точки, подставив её имя в body.point.
    principal = principal_from_session(request)
    requested_point = body.point.strip()
    if principal.is_admin:
        scoped_point = requested_point
    else:
        try:
            scoped_point = order_mod.point_name_for_key(d, principal.point_key or "")
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, f"В заявке на {d} нет вашей точки")
        if requested_point != scoped_point:
            raise HTTPException(403, "Нет доступа к другой точке")
    if not scoped_point:
        raise HTTPException(422, "Нужно выбрать точку")
    # В 1С уходят только заявки на завтра и послезавтра: предварительные D+3/D+4
    # ночью пересчитываются, документ по ним был бы преждевременным. Проверка
    # после скоупа: чужая точка обязана получить 403, а не подсказку про даты.
    _reject_outside_window(d, _business_today())
    if not _push1c_enabled():
        return JSONResponse({"enabled": False, "message": _PUSH1C_OFF_MSG}, status_code=503)
    ip = client_ip(request)
    logger.info("push1c: попытка {d} / {p} user={u} ip={ip}",
                d=d, p=scoped_point, u=principal.username, ip=ip)
    try:
        writer_mod = _load_writer()
        result = writer_mod.TransferOrderWriter().push(
            d, scoped_point, actor=principal.username, ip=ip)
        # «number» — номер созданного документа, если писатель его вернул: страница
        # показывает менеджеру «Создано: заказ №…», чтобы он нашёл его в 1С.
        return JSONResponse({"ok": True, **result,
                             "number": (result or {}).get("number")})
    except RuntimeError as e:
        # Осмысленные отказы писателя («уже записана», «заявка пустая», «1С ответила
        # HTTP …») менеджер должен видеть текстом, а не одинаковым «не получилось».
        text = str(e)[:200]
        logger.warning("push1c: отказ {d} / {p}: {e}", d=d, p=scoped_point, e=text)
        return JSONResponse({"message": text},
                            status_code=409 if "уже записана" in text else 422)
    except Exception:  # noqa: BLE001 — техошибок в UI нет никогда, детали в лог
        logger.exception("push1c: не удалось записать заявку {d} / {p}", d=d, p=scoped_point)
        return JSONResponse(
            {"message": "Записать заявку в 1С не получилось — подробности в логе сервиса"},
            status_code=502)
