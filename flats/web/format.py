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
