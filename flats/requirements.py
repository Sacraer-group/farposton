"""Propose explicit rental constraints from a short Russian brief, without saving them."""

import re
from decimal import Decimal


_MONEY = re.compile(
    r"(?:(?<![\w.,+\-−–])|(?<=до))(?P<number>(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,2})?)"
    r"\s*(?P<unit>тыс(?:яч(?:а|и|у)?)?\.?|[кk](?!\w)|руб(?:лей|ля|ль|\.)?|[₽р](?!\w))?"
)
_CATEGORIES = {
    "move_in_max": r"\b(?:на въезд|при въезде|въезд|на переезд|при переезде|стартовый бюджет)\b",
    "monthly_max": r"\b(?:в месяц|ежемесячно|аренда|аренду|месячный бюджет|бюджет)\b|/мес\b",
    "other": r"\b(?:залог|комиссия|комиссию|зарплата|зарплату|доход|ремонт|мебель|площадь|метров)\b",
}
_LABELS = {"monthly_max": "В месяц до", "move_in_max": "На въезд до",
           "rooms": "Комнаты", "pets_required": "Разрешение на животных",
           "districts": "Районы", "priority": "Приоритет"}
_PRIORITIES = {
    "budget": r"меньше платить|дешевле|сэкономить|главное.{0,12}бюджет",
    "move_in": r"легче въехать|меньше.{0,12}(?:въезд|при въезде)|минимальн.{0,12}(?:въезд|залог)",
    "house": r"работаю из дома|удаленн\w* работ\w*|работаю удаленно|отключени\w*|управляющ\w* компани\w*",
    "commute": r"дорога до|путь до|до работы|до учебы|поездк\w*|общественн\w* транспорт\w*|\bезжу\b|проверить дорогу",
}


def _prefix(text, start):
    """A nearby clause, so a negation cannot leak across another requirement."""
    return re.split(r"[,;.!?\n]", text[max(0, start - 60):start])[-1]


def _negated(text, start, end=None):
    prefix = _prefix(text, start)
    before = re.search(r"\b(?:не|ни|нельзя|без|кроме|исключая|исключить|подальше от)\s+"
                       r"(?:(?:ни|в|на|район\w*|нуж\w*|хочу|жить|вариант|подход\w*|рассматрива\w*|только)\s+){0,4}$"
                       r"|\bне\s*важн\w*\s*$", prefix)
    after = end is not None and re.match(
        r"\s+(?:мне\s+)?(?:не\s+(?:хочу|нуж\w*|подход\w*|рассматрива\w*|интерес\w*)|исключаю)\b", text[end:])
    return bool(before or after)


def _choice_exclusions(text, matches):
    """Negation before or after a joined list applies to the whole list."""
    excluded = [_negated(text, match.start(), match.end()) for match in matches]
    joined = [bool(re.fullmatch(r"\s*(?:,\s*(?:(?:и|или|ни)\s+)?|(?:и|или|ни)\s+)(?:(?:в|на)\s+)?",
                               text[left.end():right.start()])) for left, right in zip(matches, matches[1:])]
    for i in range(1, len(matches)):
        if joined[i - 1] and excluded[i - 1]:
            excluded[i] = True
    for i in reversed(range(len(matches) - 1)):
        if joined[i] and excluded[i + 1]:
            excluded[i] = True
    return {match.start(): value for match, value in zip(matches, excluded)}


def _district_pattern(name):
    words = []
    for word in re.findall(r"[а-яa-z0-9]+", name.lower().replace("ё", "е")):
        stem, endings = word, ""
        if word.endswith("ая"):
            stem, endings = word[:-2], "(?:ая|ой|ую)"
        elif word.endswith("ие"):
            stem, endings = word[:-2], "(?:ие|ия|ии|ию|ием)"
        elif word.endswith("а"):
            stem, endings = word[:-1], "(?:а|е|у|ы|ой)"
        elif word.endswith("я"):
            stem, endings = word[:-1], "(?:я|и|е|ю|ей)"
        elif word.endswith("ь"):
            stem, endings = word[:-1], "(?:ь|и|ью)"
        elif word[-1:] in "бвгджзйклмнпрстфхцчшщ":
            endings = "(?:а|е|у|ом)?"
        words.append(re.escape(stem) + endings)
    return r"\b" + r"[\s,]+".join(words) + r"\b"


def parse_requirements(text, districts=()):
    if not isinstance(text, str) or not text.strip() or len(text) > 1200:
        raise ValueError("Опишите задачу текстом от 1 до 1200 символов")
    text = text.strip()
    normalized = text.lower().replace("ё", "е")
    filters, recognized, notes = {}, [], []

    def add(field, value, snippets):
        if field != "priority":
            filters[field] = value
        recognized.append({"field": field, "label": _LABELS[field], "value": value,
                           "text": "; ".join(dict.fromkeys(snippets))})

    # Associate an amount only with a nearby explicit cost category. A small bare
    # number is not assumed to mean thousands; a lower bound is not an upper limit.
    categories = [(field, match) for field, pattern in _CATEGORIES.items()
                  for match in re.finditer(pattern, normalized)]
    amounts = list(_MONEY.finditer(normalized))
    money = {"monthly_max": [], "move_in_max": []}
    ambiguous_money = False
    for i, match in enumerate(amounts):
        before = normalized[amounts[i - 1].end() if i else 0:match.start()]
        after_end = amounts[i + 1].start() if i + 1 < len(amounts) else len(normalized)
        nearby = []
        for field, category in categories:
            if category.end() <= match.start():
                gap = normalized[category.end():match.start()]
                if category.start() < (amounts[i - 1].end() if i else 0):
                    continue
            elif category.start() >= match.end():
                gap = normalized[match.end():category.start()]
                if category.end() > after_end:
                    continue
            else:
                continue
            if len(gap) <= 24 and not re.search(r"[,;.!?\n]", gap):
                nearby.append((len(gap.strip()), field != "other", field, category))
        if nearby:
            _, _, field, category = min(nearby, key=lambda entry: entry[:3])
        else:
            # «Снять квартиру до 50 тыс.» has an explicit rental ceiling, but
            # «у меня 50 тыс.» alone does not identify a monthly or move-in limit.
            clause = _prefix(normalized, match.start())
            rental_ceiling = re.search(r"(?:квартир\w*|снять|сниму|студи\w*|однушк\w*|двушк\w*|трешк\w*)"
                                       r"[^,;.!?\n]{0,25}\b(?:до|не более|не больше)\s*$", clause)
            if not rental_ceiling:
                ambiguous_money |= bool(match["unit"])
                continue
            field, category = "monthly_max", rental_ceiling
        preceding_other = any(kind == "other" and cat.end() <= match.start()
                              and not re.search(r"[,;.!?\n]", normalized[cat.end():match.start()])
                              and len(normalized[cat.end():match.start()]) <= 12
                              for kind, cat in categories)
        if preceding_other:
            field = "other"
        if field == "other":
            if match["unit"]:
                notes.append("Залог, комиссию и другие отдельные платежи уточняйте по объявлению; они не заменяют общий лимит на въезд.")
            continue
        # Neither endpoint of a range or alternative is an explicit ceiling.
        if (re.match(r"\s*(?:[-–—]|или|до)\s*\d", normalized[match.end():])
                or (i and re.fullmatch(r"\s*(?:[-–—]|или|до)\s*", before))):
            ambiguous_money = True
            continue
        prefix = before[-50:]
        upper_bound = re.search(r"\bне (?:более|больше)\s*$", prefix)
        if not upper_bound and (re.search(r"\b(?:от|минимум|не менее|не меньше|больше|более)\s*$", prefix)
                                or re.search(r"\bне\s+(?:до\s*)?$", prefix)):
            ambiguous_money = True
            continue
        if re.search(r"\d\s*[-–—]\s*$", prefix) or re.search(
                r"\bот\s+\d[\d\s.,]*(?:тыс\w*\.?|[кk]|руб\w*|₽)?\s+до\s*$",
                normalized[max(0, match.start() - 80):match.start()]):
            ambiguous_money = True
            continue
        if re.search(r"[$€¥%]\s*$", before) or re.match(
                r"\s*(?:[$€¥%]|usd\b|eur\b|доллар\w*|евро\b|юан\w*)", normalized[match.end():]):
            ambiguous_money = True
            continue
        number = Decimal(re.sub(r"\s", "", match["number"]).replace(",", "."))
        unit = match["unit"] or ""
        value = number * (1000 if unit.startswith("тыс") or unit in ("к", "k") else 1)
        if value <= 0 or value > 1_000_000_000 or value != int(value) or (not unit and value < 1000):
            ambiguous_money = True
            continue
        snippet_start = max(0, match.start() - 18)
        snippet_end = min(len(text), match.end() + 16)
        money[field].append((int(value), text[snippet_start:snippet_end].strip()))
    for field, values in money.items():
        unique = {value for value, _ in values}
        if len(unique) == 1:
            add(field, unique.pop(), [snippet for _, snippet in values])
        elif unique:
            ambiguous_money = True
    if ambiguous_money:
        notes.append("Не все суммы однозначны. Укажите отдельно лимит в месяц и на въезд в рублях; спорные суммы не применены.")

    room_matches = []
    for pattern, value in ((r"\bстуди(?:я|ю|и|ей)\b", 0),
                           (r"\b(?:однушк|однокомнатн)\w*", 1),
                           (r"\b(?:двушк|двухкомнатн|двукомнатн)\w*", 2),
                           (r"\b(?:трешк|трехкомнатн)\w*", 3),
                           (r"\bчетырехкомнатн\w*", 4)):
        room_matches.extend((match, [value]) for match in re.finditer(pattern, normalized))
    room_numbers = r"(?<![\d\-–])([1-9]|10)(?:\s*([-–—]|или)\s*([1-9]|10))?\s*-?\s*(?:комнат\w*|комн\.?|к\b)"
    for match in re.finditer(room_numbers, normalized):
        first, last = int(match[1]), int(match[3] or match[1])
        if first <= last:
            room_matches.append((match, [first, last] if match[2] == "или" else list(range(first, last + 1))))
    rooms, excluded_rooms, snippets = [], [], []
    ambiguous_rooms = False
    room_matches.sort(key=lambda item: item[0].start())
    exclusions = _choice_exclusions(normalized, [match for match, _ in room_matches])
    for match, values in room_matches:
        prefix = _prefix(normalized, match.start())
        excluded = exclusions[match.start()]
        bounded = re.search(r"\b(?:от|до|минимум|максимум|не меньше|не менее|не больше|не более)\s*$", prefix)
        list_prefix = re.search(r"\b\d{1,2}\s*,\s*$", normalized[max(0, match.start() - 30):match.start()])
        if bounded or list_prefix:
            ambiguous_rooms = True
            notes.append("Границы или сложный список числа комнат задайте в форме; они не превращены в неполный набор вариантов.")
            continue
        if excluded:
            notes.append("Исключённые варианты комнат не добавлены в фильтр. Проверьте список комнат в форме.")
            excluded_rooms.extend(values)
            continue
        rooms.extend(values)
        snippets.append(text[match.start():match.end()])
    if set(rooms) & set(excluded_rooms):
        notes.append("Условия о числе комнат противоречат друг другу. Выберите подходящие варианты в форме.")
    elif rooms and not ambiguous_rooms:
        add("rooms", sorted(set(rooms)), snippets)

    pets = []
    pet_pattern = r"\b(?:с\s+(?:котом|кошкой|собакой|животными|питомцем)|есть\s+(?:кот|кошка|собака|питомец)|(?:можно|разрешены|нужны)\s+(?:животные|с животными)|без\s+(?:животных|питомцев)|(?:животных|питомцев)\s+нет)\b"
    for match in re.finditer(pet_pattern, normalized):
        phrase = match.group()
        negative = phrase.startswith("без ") or phrase.endswith(" нет")
        if _negated(normalized, match.start(), match.end()):
            notes.append("Условие о животных неоднозначно; проверьте флажок в форме.")
            continue
        pets.append((not negative, text[match.start():match.end()]))
    pet_values = {value for value, _ in pets}
    if len(pet_values) == 1:
        add("pets_required", pet_values.pop(), [snippet for _, snippet in pets])
    elif pet_values:
        notes.append("В тексте противоречат друг другу условия о животных; выберите нужное в форме.")

    selected_districts, excluded_districts, district_snippets = [], [], []
    district_matches = []
    for district in dict.fromkeys(districts):
        if not isinstance(district, str) or not district.strip():
            continue
        district_matches.extend((match, district) for match in re.finditer(_district_pattern(district), normalized))
    district_matches.sort(key=lambda item: item[0].start())
    exclusions = _choice_exclusions(normalized, [match for match, _ in district_matches])
    for match, district in district_matches:
        prefix = _prefix(normalized, match.start())
        excluded = exclusions[match.start()]
        if excluded:
            notes.append(f"Исключение района «{district}» не применено: выберите подходящие районы в форме.")
            excluded_districts.append(district)
        elif re.search(r"\b(?:работ\w*|офис\w*|учусь|учеб\w*|школ\w*|рядом с|около|ближе к|до)\b[^,;.!?\n]{0,22}$", prefix):
            notes.append(f"Путь или близость к району «{district}» проверьте в разделе поездок; это не район поиска квартиры.")
        else:
            selected_districts.append(district)
            district_snippets.append(text[match.start():match.end()])
    if set(selected_districts) & set(excluded_districts):
        notes.append("Условия о районах противоречат друг другу. Выберите подходящие районы в форме.")
    elif selected_districts:
        add("districts", sorted(set(selected_districts)), district_snippets)

    priorities = [(priority, match) for priority, pattern in _PRIORITIES.items()
                  for match in re.finditer(pattern, normalized) if not _negated(normalized, match.start())]
    priority_values = {priority for priority, _ in priorities}
    priority = next(iter(priority_values)) if len(priority_values) == 1 else None
    if priority:
        add("priority", priority, [text[match.start():match.end()] for _, match in priorities])
    elif priority_values:
        notes.append("Указано несколько приоритетов. Выберите главный в форме — остальные пожелания останутся в тексте.")
    if re.search(r"без (?:залога|комиссии)|коммунал\w*|коммунальн\w*", normalized):
        notes.append("Пожелания к залогу, комиссии и коммунальным платежам не стали отдельными фильтрами. Сверьте составляющие расходов в предложении.")
    notes.append("Тишина, школа рядом, интернет, состояние дома и время в пути не подтверждаются разбором текста. Проверьте источники, поездки и вопросы к собственнику.")
    if not recognized:
        notes.insert(0, "Явных условий для фильтров не найдено. Заполните форму; текст останется вопросом помощнику.")
    return {"mode": "rules", "filters": filters, "priority": priority, "recognized": recognized,
            "notes": list(dict.fromkeys(notes)),
            "notice": "Разобраны явные условия текста. Проверьте их перед применением."}
