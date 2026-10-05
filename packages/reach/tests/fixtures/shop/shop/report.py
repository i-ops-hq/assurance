"""The monthly report: every invoice, totalled."""

from shop.invoice import Invoice


def monthly(invoices: list[Invoice]) -> int:
    return sum(invoice.total().cents for invoice in invoices)
