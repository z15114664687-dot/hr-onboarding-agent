from app.services.policy_table import list_policy_coefficient_rows


def test_policy_coefficient_rows_are_synthetic() -> None:
    rows = list_policy_coefficient_rows()
    cities = {row.city for row in rows}

    assert cities == {"示例市", "Demo City"}
    assert all("demo" in row.note.lower() or "fictional" in row.note.lower() for row in rows)


def test_policy_coefficient_rows_filter_city() -> None:
    rows = list_policy_coefficient_rows(city="示例市")

    assert rows
    assert {row.city for row in rows} == {"示例市"}
