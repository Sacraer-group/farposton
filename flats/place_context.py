"""Small, source-attributed local corpus; address joins are exact, never semantic."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _corpus():
    return json.loads((Path(__file__).with_name("demo") / "place_context.json").read_text())


def house_facts(address: str | None) -> list[dict]:
    if not address:
        return []
    key = " ".join(address.casefold().split())
    return [dict(fact) for house in _corpus()["houses"]
            if " ".join(house["address"].casefold().split()) == key
            for fact in house["facts"]]


def local_guides(priority: str, question: str = "") -> list[dict]:
    query = question.casefold()
    guides = _corpus()["guides"]
    selected = [g for g in guides if priority in g["priorities"]
                or any(topic in query for topic in g["topics"])]
    return [{k: v for k, v in guide.items() if k not in {"topics", "priorities"}}
            for guide in selected[:3]]
