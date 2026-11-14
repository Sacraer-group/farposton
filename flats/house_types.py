from dataclasses import dataclass


@dataclass(frozen=True)
class HouseAddress:
    city: str
    street_type: str
    street_name: str
    house_number: str
    corpus: str = ""
    structure: str = ""
