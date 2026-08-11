from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PolicyCoefficientRow:
    city: str
    policy_type: str
    period: str
    lower_limit: str
    upper_limit: str
    employer_ratio: str
    employee_ratio: str
    previous_value: str
    current_value: str
    change_amount: str
    source: str
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


POLICY_SOURCE_INDEX = "examples/knowledge_base/demo_policy.md"

# These rows are deliberately fictional. Replace them with reviewed, dated policy
# sources in a private deployment; do not use them for payroll or legal decisions.
POLICY_COEFFICIENT_ROWS: tuple[PolicyCoefficientRow, ...] = (
    PolicyCoefficientRow(
        city="示例市",
        policy_type="示例缴费基数",
        period="DEMO-2026",
        lower_limit="1000",
        upper_limit="5000",
        employer_ratio="demo-only",
        employee_ratio="demo-only",
        previous_value="not applicable",
        current_value="synthetic range 1000-5000",
        change_amount="not applicable",
        source="Synthetic onboarding policy",
        note="Fictional data for UI demonstration only.",
    ),
    PolicyCoefficientRow(
        city="Demo City",
        policy_type="Sample benefits contribution",
        period="DEMO-2026",
        lower_limit="2000",
        upper_limit="8000",
        employer_ratio="demo-only",
        employee_ratio="demo-only",
        previous_value="not applicable",
        current_value="synthetic range 2000-8000",
        change_amount="not applicable",
        source="Synthetic onboarding policy",
        note="Fictional data for UI demonstration only.",
    ),
)


def list_policy_coefficient_rows(*, city: str | None = None) -> list[PolicyCoefficientRow]:
    """Return synthetic policy rows for the demo UI."""

    if not city:
        return list(POLICY_COEFFICIENT_ROWS)
    return [row for row in POLICY_COEFFICIENT_ROWS if row.city == city]
