from pathlib import Path

from app.services.knowledge_base import load_knowledge_chunks, retrieve_relevant_chunks


def test_retrieve_relevant_chunks_from_markdown(tmp_path) -> None:
    source = tmp_path / "policy.md"
    source.write_text("上海社保缴费基数下限为7460元，上限为37302元。", encoding="utf-8")
    load_knowledge_chunks.cache_clear()

    chunks = retrieve_relevant_chunks("上海社保基数是多少", str(tmp_path))

    assert chunks
    assert "7460" in chunks[0].text


def test_salary_standard_query_prioritizes_minimum_wage_policy(tmp_path) -> None:
    minimum_wage = tmp_path / "北京_2025最低工资标准.md"
    minimum_wage.write_text("北京市最低工资标准：每月不低于2540元。", encoding="utf-8")
    housing_fund = tmp_path / "北京_2025住房公积金年度缴存.md"
    housing_fund.write_text("住房公积金月缴存基数上限为35811元，下限为2540元。", encoding="utf-8")
    unrelated_city = tmp_path / "深圳_2025住房公积金缴存基数.md"
    unrelated_city.write_text("深圳住房公积金缴存基数说明，页面导航提到北京。", encoding="utf-8")
    load_knowledge_chunks.cache_clear()

    chunks = retrieve_relevant_chunks("北京的薪酬标准是什么", str(tmp_path))

    assert chunks
    assert chunks[0].title == "北京_2025最低工资标准"
    assert chunks[1].title != "深圳_2025住房公积金缴存基数"


def test_configured_missing_dir_can_fallback_to_project_example_knowledge_base(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    flow_dir = project_root / "examples" / "knowledge_base"
    flow_dir.mkdir(parents=True)
    source = flow_dir / "转正手续办理指引.md"
    source.write_text("转正条件包括完成入职培训和合规考试。", encoding="utf-8")
    monkeypatch.chdir(project_root)
    load_knowledge_chunks.cache_clear()

    chunks = retrieve_relevant_chunks("试用期转正标准", "missing/knowledge-base")

    assert chunks
    assert "合规考试" in chunks[0].text


def test_formal_employee_materials_are_hidden_from_candidates(tmp_path) -> None:
    source = tmp_path / "formal_benefits.md"
    source.write_text(
        """---
audience: formal_employee
employee_status: 正式员工
---

正式员工补充商业保险覆盖门急诊和住院保障。
""",
        encoding="utf-8",
    )
    load_knowledge_chunks.cache_clear()

    candidate_chunks = retrieve_relevant_chunks(
        "补充商业保险",
        str(tmp_path),
        user_tags={"employee_status": "candidate"},
    )
    formal_chunks = retrieve_relevant_chunks(
        "补充商业保险",
        str(tmp_path),
        user_tags={"employee_status": "formal_employee"},
    )

    assert candidate_chunks == []
    assert formal_chunks
    assert "补充商业保险" in formal_chunks[0].text


def test_section_tags_require_matching_user_dimension(tmp_path) -> None:
    source = tmp_path / "formal_benefits.md"
    source.write_text(
        """---
audience: formal_employee
employee_status: 正式员工
department: 通用
---

# 正式员工福利

## 产品与技术专项福利

<!-- tags: department=产品与技术 -->

岗位资格支持适用于产品与技术正式员工。
""",
        encoding="utf-8",
    )
    load_knowledge_chunks.cache_clear()

    unmatched_chunks = retrieve_relevant_chunks(
        "岗位资格支持",
        str(tmp_path),
        user_tags={"employee_status": "formal_employee", "department": "销售条线"},
    )
    missing_department_chunks = retrieve_relevant_chunks(
        "岗位资格支持",
        str(tmp_path),
        user_tags={"employee_status": "formal_employee"},
    )
    matched_chunks = retrieve_relevant_chunks(
        "岗位资格支持",
        str(tmp_path),
        user_tags={"employee_status": "formal_employee", "department": "产品与技术"},
    )

    assert unmatched_chunks == []
    assert missing_department_chunks == []
    assert matched_chunks
    assert "产品与技术" in matched_chunks[0].text


def test_formal_employee_query_prioritizes_formal_material(tmp_path) -> None:
    old_source = tmp_path / "new_hire_guide.md"
    old_source.write_text("新员工可了解补充商业保险的基础流程。", encoding="utf-8")
    formal_source = tmp_path / "formal_benefits.md"
    formal_source.write_text(
        """---
audience: formal_employee
employee_status: 正式员工
---

正式员工补充商业保险包含门急诊、住院和意外保障方向。
""",
        encoding="utf-8",
    )
    load_knowledge_chunks.cache_clear()

    chunks = retrieve_relevant_chunks(
        "正式员工补充商业保险",
        str(tmp_path),
        user_tags={"employee_status": "formal_employee"},
    )

    assert chunks
    assert chunks[0].title == "formal_benefits"


def test_requested_other_department_hides_internal_benefits(tmp_path) -> None:
    source = tmp_path / "formal_benefits.md"
    source.write_text(
        """---
audience: formal_employee
employee_status: 正式员工
department: 通用
sensitivity: internal_benefits
---

产品与技术岗位资格支持适用于投研岗位正式员工。
""",
        encoding="utf-8",
    )
    load_knowledge_chunks.cache_clear()

    sales_chunks = retrieve_relevant_chunks(
        "产品与技术岗位资格支持",
        str(tmp_path),
        user_tags={"employee_status": "formal_employee", "department": "销售条线"},
    )
    investment_chunks = retrieve_relevant_chunks(
        "产品与技术岗位资格支持",
        str(tmp_path),
        user_tags={"employee_status": "formal_employee", "department": "产品与技术"},
    )

    assert sales_chunks == []
    assert investment_chunks


def test_synthetic_demo_materials_enforce_formal_policy_isolation() -> None:
    flow_dir = Path(__file__).resolve().parents[1] / "examples" / "knowledge_base"
    load_knowledge_chunks.cache_clear()

    candidate_chunks = retrieve_relevant_chunks(
        "Synthetic formal employee travel policy",
        str(flow_dir),
        user_tags={"employee_status": ["candidate", "候选人"], "department": "产品与技术", "city": "示例市", "job_level": "L2"},
    )
    formal_chunks = retrieve_relevant_chunks(
        "Synthetic formal employee travel policy 900 demo credits",
        str(flow_dir),
        user_tags={"employee_status": "formal_employee", "department": "产品与技术", "city": "示例市", "job_level": "L5"},
    )

    assert not any("formal_employee_demo_policy" in chunk.path for chunk in candidate_chunks)
    assert formal_chunks
    assert formal_chunks[0].title == "formal_employee_demo_policy"
    assert "900 demo credits" in formal_chunks[0].text
