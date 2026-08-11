from app.adapters.feishu_im import (
    material_request_card,
    material_review_overall_summary_card,
    material_review_result_card,
    material_review_summary_card,
    onboarding_chat_intro_card,
    overdue_node_card,
    progress_card,
    qa_answer_card,
    welcome_card,
    welcome_text,
)


def test_overdue_node_card_contains_actionable_fields() -> None:
    card = overdue_node_card(
        candidate_name="张三",
        node_name="medical_check",
        due_date="2026-06-01",
        overdue_days=4,
        escalation_level=2,
    )

    content = card["elements"][0]["content"]
    assert card["header"]["template"] == "red"
    assert "张三" in content
    assert "体检" in content
    assert "4 天" in content
    assert "升级提醒" in content


def test_material_review_result_card_uses_candidate_feedback() -> None:
    card = material_review_result_card(
        candidate_name="李四",
        material_type="resignation_certificate",
        decision="auto_rejected",
        message_to_candidate="请补充带公章的离职证明。",
        failed_checks=["盖章识别结果：未检测到盖章"],
    )

    content = card["elements"][0]["content"]
    assert card["header"]["template"] == "red"
    assert card["header"]["title"]["content"] == "材料未通过"
    assert "离职证明" in content
    assert "请补充带公章的离职证明" in content
    assert "未检测到盖章" in content


def test_material_review_result_card_keeps_supplement_title_for_missing_requirements() -> None:
    card = material_review_result_card(
        candidate_name="李四",
        material_type="award_certificate",
        decision="auto_rejected",
        message_to_candidate="本次获奖证明已通过单份材料检查，但还缺：数学建模二等奖。请继续上传缺少的文件。",
        failed_checks=["需继续补充材料：还缺数学建模二等奖"],
    )

    assert card["header"]["title"]["content"] == "材料需补充"


def test_material_review_summary_card_lists_batch_results() -> None:
    card = material_review_summary_card(
        candidate_name="李四",
        material_type="award_certificate",
        results=[
            {"file_name": "优秀学生奖学金.pdf", "decision": "auto_approved", "message": "材料已通过自动审核。"},
            {"file_name": "数学建模二等奖.pdf", "decision": "auto_rejected", "message": "还缺：公章信息。请继续上传缺少的文件。"},
        ],
        action_url="https://example.com/materials",
    )

    content = card["elements"][0]["content"]
    assert card["header"]["title"]["content"] == "材料批量审核结果"
    assert "本次提交**：2 个文件" in content
    assert "文件已通过 2 个；仍需补充 1 项；未通过 0 个；人工复核 0 个" in content
    assert "优秀学生奖学金.pdf：已通过" in content
    assert "数学建模二等奖.pdf：文件已通过；仍需补充：公章信息" in content
    assert "仍需补充**" in content
    assert _card_actions(card)[0]["text"]["content"] == "查看入职进度"


def test_material_review_overall_summary_card_groups_material_results() -> None:
    card = material_review_overall_summary_card(
        candidate_name="李四",
        groups=[
            {
                "material_type": "resume",
                "material_name": "求职简历",
                "results": [
                    {"file_name": "简历.pdf", "decision": "auto_approved", "message": "材料已通过自动审核。"},
                ],
            },
            {
                "material_type": "award_certificate",
                "material_name": "获奖证明",
                "results": [
                    {"file_name": "奖学金.pdf", "decision": "auto_rejected", "message": "还缺：优秀学生。请继续上传缺少的文件。"},
                    {"file_name": "竞赛.pdf", "decision": "manual_review", "message": "材料已收到，将由 HR 人工审核。"},
                ],
            },
        ],
        action_url="https://example.com/materials",
    )

    content = card["elements"][0]["content"]
    assert card["header"]["title"]["content"] == "材料统一提交结果"
    assert "本次提交**：2 个材料项，3 个文件" in content
    assert "已提交 3 个；文件已通过 2 个；仍需补充 1 项；已驳回 0 个；需复核 1 个" in content
    assert "求职简历：提交 1 个；文件已通过 1 个" in content
    assert "获奖证明：提交 2 个；文件已通过 1 个；仍需补充 1 项" in content
    assert "获奖证明 / 奖学金.pdf：文件已通过；仍需补充：优秀学生" in content
    assert "获奖证明：优秀学生" in content
    assert _card_actions(card)[0]["text"]["content"] == "查看入职进度"


def test_progress_card_has_detail_and_material_buttons() -> None:
    card = progress_card(
        {
            "current_stage": "体检",
            "overall_status": "进行中",
            "next_step": "预入职材料",
            "next_due_date": "2026-06-01",
            "approved_count": 2,
            "total_count": 8,
            "nodes": [{"node_name": "体检", "status": "进行中", "due_date": "2026-06-01"}],
        },
        detail_url="https://example.com/detail",
        material_url="https://example.com/materials",
        confirm_url="https://example.com/confirm",
    )

    assert "当前阶段" in card["elements"][0]["content"]
    actions = _card_actions(card)
    assert [action["text"]["content"] for action in actions] == ["进入入职工作台", "确认当前节点"]


def test_candidate_overdue_card_hides_hr_dashboard_action() -> None:
    card = overdue_node_card(
        candidate_name="张三",
        node_name="document_submission",
        due_date="2026-06-01",
        overdue_days=2,
        escalation_level=1,
        action_url="https://example.com/materials",
        audience="candidate",
        action_text="打开材料提交页",
    )

    assert "材料提交页" in card["elements"][0]["content"]
    assert "打开 HR 后台" not in str(card)
    assert _card_actions(card)[0]["text"]["content"] == "打开材料提交页"


def test_material_request_card_has_submit_button() -> None:
    card = material_request_card(
        ["身份证件：待提交", "体检报告：已通过"],
        candidate_name="王五",
        employment_type="社招",
        submit_url="https://example.com/materials",
    )

    assert "王五" in card["elements"][0]["content"]
    assert _card_actions(card)[0]["text"]["content"] == "进入入职工作台"


def test_welcome_card_has_candidate_entry_actions() -> None:
    card = welcome_card(
        "张三",
        "2026-06-08",
        detail_url="https://example.com/workspace",
        material_url="https://example.com/materials",
    )

    assert card["header"]["title"]["content"] == "入职助手"
    assert "张三" in card["elements"][0]["content"]
    assert "进度" in card["elements"][0]["content"]
    assert [action["text"]["content"] for action in _card_actions(card)] == ["打开入职工作台", "提交材料"]


def test_welcome_text_has_candidate_entry_links() -> None:
    text = welcome_text(
        "张三",
        "2026-06-08",
        detail_url="https://example.com/workspace",
        material_url="https://example.com/materials",
    )

    assert "张三你好" in text
    assert "入职协同助手" in text
    assert "进度" in text
    assert "https://example.com/workspace" in text
    assert "https://example.com/materials" in text


def test_onboarding_chat_intro_card_describes_group_usage() -> None:
    card = onboarding_chat_intro_card("张三", "2026-06-08", detail_url="https://example.com/workspace")

    content = card["elements"][0]["content"]
    assert card["header"]["title"]["content"] == "入职协同群已创建"
    assert "候选人可以查看进度" in content
    assert "HRBP/负责人" in content
    assert _card_actions(card)[0]["text"]["content"] == "打开入职工作台"


def test_qa_answer_card_renders_markdown_and_sources() -> None:
    card = qa_answer_card(
        question="北京的福利标准",
        answer="**北京** 当前按公司政策执行。",
        sources=[{"id": "S1", "title": "北京福利政策", "path": "/policy/beijing.md"}],
    )

    assert card["header"]["title"]["content"] == "智能问答"
    assert card["header"]["template"] == "blue"
    assert "**北京**" in card["elements"][0]["content"]
    assert "北京福利政策" in card["elements"][2]["content"]


def test_qa_answer_card_renders_external_search_fallback_source() -> None:
    card = qa_answer_card(
        question="英国福利待遇",
        answer="英国福利待遇以当地官方口径为准。",
        used_external_search=True,
    )

    assert "参考来源" in card["elements"][2]["content"]
    assert "外部联网检索" in card["elements"][2]["content"]


def test_qa_answer_card_converts_markdown_table_to_feishu_table() -> None:
    answer = (
        "| 项目 | 2024口径 | 2025口径 | 变化 |\n"
        "| --- | --- | --- | --- |\n"
        "| 社保下限 | 7384 | 7460 | +76 |"
    )

    card = qa_answer_card(question="上海社保近两年对比", answer=answer, used_external_search=True)

    table = next(element for element in card["elements"] if element.get("tag") == "table")
    assert table["row_height"] == "low"
    assert table["freeze_first_column"] is True
    assert table["columns"][0]["display_name"] == "项目"
    assert table["columns"][0]["horizontal_align"] == "left"
    assert "width" not in table["columns"][0]
    assert table["columns"][3]["display_name"] == "变化"
    assert table["rows"][0]["col_0"] == "社保下限"
    assert table["rows"][0]["col_3"] == "+76"
    assert "| --- | --- | --- | --- |" not in str(card)


def test_qa_answer_card_keeps_text_around_markdown_table() -> None:
    answer = (
        "以下是对比：\n\n"
        "| 项目 | 旧值 | 新值 |\n"
        "| --- | --- | --- |\n"
        "| 社保下限 | 4224 | 4498 |\n\n"
        "结论：整体上升。"
    )

    card = qa_answer_card(question="武汉社保近两年对比", answer=answer)

    markdown_contents = "\n".join(element["content"] for element in card["elements"] if element.get("tag") == "markdown")
    assert "以下是对比" in markdown_contents
    assert "结论：整体上升" in markdown_contents
    assert any(element.get("tag") == "table" for element in card["elements"])


def test_qa_answer_card_splits_long_answer_without_early_truncation() -> None:
    answer = "第一段。" + ("这是较长回答。" * 260)
    card = qa_answer_card(question="哈尔滨的社保标准", answer=answer)

    markdown_contents = [element["content"] for element in card["elements"] if element.get("tag") == "markdown"]
    joined = "\n".join(markdown_contents)

    assert len(markdown_contents) > 1
    assert "这是较长回答" in joined
    assert not markdown_contents[0].endswith("...")


def _card_actions(card: dict) -> list[dict]:
    return next(element["actions"] for element in card["elements"] if element.get("tag") == "action")
