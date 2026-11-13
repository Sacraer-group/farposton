from flats.money import (
    parse_commission,
    parse_deposit,
    parse_prepayment,
    utilities_included,
)
from flats.types import Costs, Offer


def compute_costs(offer: Offer) -> Costs:
    rent = offer.price
    prepayment = parse_prepayment(offer.prepayment_raw, rent)
    deposit = parse_deposit(offer.deposit_raw, rent)
    commission = parse_commission(offer.commission_raw, rent)
    components = (
        ("prepayment", prepayment),
        ("deposit", deposit),
        ("commission", commission),
    )
    known = [amount.rub for _, amount in components if amount.rub is not None]

    monthly_unknown = []
    if rent is None:
        monthly_unknown.append("rent")
    if utilities_included(offer.utilities_raw) is not True:
        monthly_unknown.append("utilities")

    return Costs(
        rent=rent,
        monthly_known=rent,
        monthly_unknown=tuple(monthly_unknown),
        prepayment=prepayment,
        deposit=deposit,
        commission=commission,
        move_in_known=sum(known) if known else None,
        move_in_unknown=tuple(
            name for name, amount in components if amount.rub is None
        ),
    )
