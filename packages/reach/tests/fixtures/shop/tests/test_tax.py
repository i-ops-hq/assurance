from shop.tax import tax


def test_tax_on_ten() -> None:
    assert tax(10.0).cents == 80
