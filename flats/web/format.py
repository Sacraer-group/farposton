def rub(n: int | None) -> str:
    if n is None:
        return "не указано"
    return f"{n:,} ₽".replace(",", " ")


def status_label(s: str) -> str:
    return {
        "match": "Подходит",
        "unknown": "Нужно уточнить",
        "reject": "Не подходит",
    }[s]


def part_label(name: str) -> str:
    return {"prepayment": "предоплата", "deposit": "залог", "commission": "комиссия",
            "rent": "аренда", "utilities": "коммунальные"}.get(name, name)


def room_label(rooms: int | None) -> str:
    if rooms is None:
        return "Комнаты не указаны"
    return "Студия" if rooms == 0 else f"{rooms}-комн."
