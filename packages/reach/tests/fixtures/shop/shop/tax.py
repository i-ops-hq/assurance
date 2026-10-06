"""Sales tax on a total."""

from shop.money import Money, to_cents

RATE = 0.08


def tax(total: float) -> Money:
    return Money(to_cents(total * RATE))
