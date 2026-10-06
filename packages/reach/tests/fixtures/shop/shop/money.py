"""Money in cents, so a total never drifts by a float."""


def to_cents(amount: float) -> int:
    return int(round(amount * 100))


class Money:
    def __init__(self, cents: int) -> None:
        self.cents = cents

    def plus(self, other: "Money") -> "Money":
        return Money(self.cents + other.cents)
