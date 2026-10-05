"""Print this month's total."""

from shop.invoice import Invoice
from shop.report import monthly


def main() -> None:
    print(monthly([Invoice([10.0, 2.5])]))
