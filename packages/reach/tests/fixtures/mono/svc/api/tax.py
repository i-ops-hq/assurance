from svc.api.money import money


def tax(cents: int) -> int:
    return money(cents) // 10
