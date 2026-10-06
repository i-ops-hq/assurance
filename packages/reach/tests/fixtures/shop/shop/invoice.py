"""An invoice: lines, a subtotal, the tax on it."""

from shop.money import Money
from shop.tax import tax


class Invoice:
    def __init__(self, lines: list[float]) -> None:
        self.lines = lines

    def subtotal(self) -> float:
        return sum(self.lines)

    def total(self) -> Money:
        return Money(int(self.subtotal() * 100)).plus(tax(self.subtotal()))
