import json
from datetime import date, datetime

from app.adapters.feishu_bitable import (
    candidate_case_to_bitable_fields,
    material_to_bitable_fields,
    _serialize_fields,
    normalize_bitable_ids,
    workflow_node_to_bitable_fields,
)
from app.models.onboarding import CandidateCase, MaterialSubmission, WorkflowNode


def test_normalize_bitable_ids_accepts_copied_url() -> None:
    app_token, table_id = normalize_bitable_ids(
        "https://tenant.example/wiki/demoWikiToken000001/tables/tblDemoTable000001?view=vewDemo000001",
        "tblFallback",
    )

    assert app_token == "demoWikiToken000001"
    assert table_id == "tblDemoTable000001"


def test_normalize_bitable_ids_accepts_query_fragment_table_id() -> None:
    app_token, table_id = normalize_bitable_ids(
        "base_token",
        "table=tble290As4J788Ll&view=vewSvnr8md",
    )

    assert app_token == "base_token"
    assert table_id == "tble290As4J788Ll"


def test_serialize_fields_uses_feishu_timestamp_millis() -> None:
    fields = _serialize_fields({"预计入职日期": date(2026, 6, 4)})

    assert fields == {"预计入职日期": 1780531200000}


def test_serialize_fields_wraps_feishu_user_fields() -> None:
    fields = _serialize_fields(
        {
            "HRBP": "ou_hrbp",
            "负责人OpenID": "ou_owner",
            "飞书OpenID": "ou_candidate",
        }
    )

    assert fields["HRBP"] == [{"id": "ou_hrbp"}]
    assert fields["负责人OpenID"] == [{"id": "ou_owner"}]
    assert fields["飞书OpenID"] == "ou_candidate"


def test_workflow_node_mapping_uses_existing_select_labels() -> None:
    node = WorkflowNode(
        case_id=1,
        node_name="medical_check",
        due_date=date(2026, 5, 30),
        status="pending",
        overdue_days=0,
        escalation_level=0,
    )

    fields = workflow_node_to_bitable_fields(node)

    assert fields["候选人ID"] == "1"
    assert fields["节点分组"] == "Offer后准备"
    assert fields["节点编码"] == "medical_check"
    assert fields["节点名称"] == "体检"
    assert fields["状态"] == "进行中"
    assert fields["提醒状态"] == "未提醒"
    assert fields["催办次数"] == 0
    assert fields["升级等级"] == "普通"


def test_workflow_node_mapping_groups_material_nodes() -> None:
    node = WorkflowNode(
        case_id=1,
        node_name="document_submission",
        due_date=date(2026, 6, 1),
        status="pending",
    )

    fields = workflow_node_to_bitable_fields(node)

    assert fields["节点分组"] == "材料递交"
    assert fields["节点名称"] == "材料递交-预入职材料"


def test_submitted_node_counts_as_done_for_bitable_status() -> None:
    node = WorkflowNode(
        case_id=1,
        node_name="medical_check",
        due_date=date(2026, 5, 30),
        status="submitted",
    )

    fields = workflow_node_to_bitable_fields(node)

    assert fields["状态"] == "已完成"


def test_candidate_mapping_uses_summary_labels() -> None:
    case = CandidateCase(
        id=1,
        candidate_name="张三",
        feishu_open_id="ou_test",
        employment_type="校招-境内",
        job_level="P6",
        expected_onboard_date=date(2026, 6, 4),
        current_stage="medical_check",
        overall_status="pending",
        risk_level="low",
    )
    case.nodes = [
        WorkflowNode(
            id=1,
            case_id=1,
            node_name="background_check",
            due_date=date(2026, 5, 28),
            status="approved",
        ),
        WorkflowNode(
            id=2,
            case_id=1,
            node_name="medical_check",
            due_date=date(2026, 6, 1),
            status="pending",
        ),
    ]
    case.materials = []

    fields = candidate_case_to_bitable_fields(case)

    assert fields["当前阶段"] == "Offer后准备"
    assert fields["当前节点"] == "体检"
    assert fields["下一截止日期"] == date(2026, 6, 1)
    assert fields["招聘类型"] == "校招-境内"
    assert fields["职级"] == "P6"
    assert fields["超时节点数"] == 0
    assert fields["风险等级"] == "低"
    assert fields["整体状态"] == "待启动"
    assert fields["材料进度"] == "0/12 已通过，12 待提交，0 需处理"


def test_material_mapping_uses_display_labels() -> None:
    case = CandidateCase(
        id=1,
        candidate_name="张三",
        employment_type="校招-境内",
        expected_onboard_date=date(2026, 6, 4),
    )
    material = MaterialSubmission(
        case_id=1,
        material_type="medical_report",
        file_name="体检报告.pdf",
        review_status="auto_rejected",
        review_reason="需包含总检结论页",
        retry_count=1,
        created_at=datetime(2026, 5, 27, 9, 30, 0),
    )
    material.case = case

    fields = material_to_bitable_fields(material)

    assert fields["候选人ID"] == "1"
    assert fields["候选人姓名"] == "张三"
    assert fields["材料类型"] == "体检报告"
    assert fields["材料编码"] == "medical_report"
    assert fields["适用用工类型"] == "校招-境内"
    assert fields["审核状态"] == "需补充"
    assert fields["提交状态"] == "需补充"
    assert fields["候选人反馈"] == "需包含总检结论页"
    assert fields["最近提交时间"] == datetime(2026, 5, 27, 9, 30, 0)


def test_material_mapping_falls_back_to_catalog_name() -> None:
    material = MaterialSubmission(
        case_id=1,
        material_type="bank_card",
        file_name="银行卡.jpg",
        review_status="pending",
    )

    fields = material_to_bitable_fields(material)

    assert fields["材料类型"] == "工资卡"
    assert fields["材料编码"] == "bank_card"


def test_resume_material_mapping_uses_resume_label() -> None:
    material = MaterialSubmission(
        case_id=1,
        material_type="resume",
        file_name="求职简历.pdf",
        extracted_fields_json='{"求学经历":["某大学"],"职称资格":["CPA"]}',
        review_status="auto_approved",
    )

    fields = material_to_bitable_fields(material)

    assert fields["候选人ID"] == "1"
    assert fields["材料类型"] == "求职简历"
    assert fields["材料编码"] == "resume"
    assert "CPA" in fields["提取字段JSON"]


def test_resume_material_mapping_writes_structured_ocr_summary() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", employment_type="校招-境内")
    material = MaterialSubmission(
        case_id=1,
        material_type="resume",
        file_name="个人简历.pdf",
        ocr_text="很长的简历全文" * 200,
        extracted_fields_json=json.dumps(
            {
                "姓名": "林晓安",
                "求学经历": [{"学校": "星河大学"}],
                "获奖信息": [{"奖项名称": "优秀学生丙等奖学金"}],
                "实习经历": [{"公司": "字节跳动", "岗位": "商业分析实习生", "时间": "2025.01-2025.04"}],
                "工作经历": [{"公司": "易方达基金", "岗位": "产品经理", "时间": "2025.05-至今"}],
            },
            ensure_ascii=False,
        ),
        review_status="auto_approved",
    )
    material.case = case

    fields = material_to_bitable_fields(material)

    assert "很长的简历全文" not in fields["OCR文本"]
    assert "姓名：林晓安" in fields["OCR文本"]
    assert "学历学校：星河大学" in fields["OCR文本"]
    assert "奖项：优秀学生丙等奖学金" in fields["OCR文本"]
    assert "字节跳动 / 商业分析实习生 / 2025.01-2025.04" in fields["OCR文本"]
    assert "易方达基金 / 产品经理 / 2025.05-至今" in fields["OCR文本"]


def test_resume_material_mapping_extracts_summary_from_ocr_text() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", employment_type="校招-境内")
    resume_text = """
    林晓安
    教育经历
    2021.09-2025.06 星河大学 金融学
    获奖信息
    2024 优秀学生丙等奖学金
    2023 数学建模二等奖
    实习经历
    2025.01-2025.04 字节跳动商业分析实习生
    """
    material = MaterialSubmission(
        case_id=1,
        material_type="resume",
        file_name="个人简历.pdf",
        ocr_text=resume_text,
        extracted_fields_json=json.dumps({"姓名": "林晓安", "简历摘要": resume_text[:500]}, ensure_ascii=False),
        review_status="auto_approved",
    )
    material.case = case

    fields = material_to_bitable_fields(material)
    extracted_summary = json.loads(fields["提取字段JSON"])

    assert "姓名：林晓安" in fields["OCR文本"]
    assert "学历学校：星河大学" in fields["OCR文本"]
    assert "优秀学生丙等奖学金" in fields["OCR文本"]
    assert "数学建模二等奖" in fields["OCR文本"]
    assert "简历摘要" not in extracted_summary
    assert extracted_summary["奖项"] == ["优秀学生丙等奖学金", "数学建模二等奖"]


def test_text_heavy_material_mapping_uses_field_summary_not_full_text() -> None:
    material = MaterialSubmission(
        case_id=1,
        material_type="medical_report",
        file_name="体检报告.pdf",
        ocr_text="很长的体检报告全文" * 200,
        extracted_fields_json=json.dumps(
            {
                "姓名": "林晓安",
                "体检日期": "2026-05-20",
                "体检机构": "某体检中心",
                "总检结论": "未见明显异常",
                "正文": "很长的体检报告全文" * 200,
            },
            ensure_ascii=False,
        ),
        review_status="auto_approved",
    )

    fields = material_to_bitable_fields(material)
    extracted_summary = json.loads(fields["提取字段JSON"])

    assert "很长的体检报告全文" not in fields["OCR文本"]
    assert "体检机构：某体检中心" in fields["OCR文本"]
    assert "正文" not in extracted_summary
    assert extracted_summary["总检结论"] == "未见明显异常"


def test_award_material_mapping_includes_issue_date_alias() -> None:
    material = MaterialSubmission(
        case_id=1,
        material_type="award_certificate",
        file_name="优秀学生.pdf",
        extracted_fields_json=json.dumps(
            {
                "姓名": "林晓安",
                "奖项名称": "优秀学生",
                "发证机构": "星河大学",
                "颁发日期": "2025-06-01",
            },
            ensure_ascii=False,
        ),
        review_status="auto_approved",
    )

    fields = material_to_bitable_fields(material)
    extracted_summary = json.loads(fields["提取字段JSON"])

    assert "发放时间：2025-06-01" in fields["OCR文本"]
    assert extracted_summary["发放时间"] == "2025-06-01"


def test_award_material_mapping_marks_english_translation_note() -> None:
    material = MaterialSubmission(
        case_id=1,
        material_type="award_certificate",
        file_name="Outstanding Student Scholarship.pdf",
        ocr_text="Certificate awarded to LIN Xiaoan for Outstanding Student Scholarship by Xinghe University.",
        extracted_fields_json=json.dumps(
            {
                "姓名": "LIN Xiaoan",
                "奖项名称": "优秀学生奖学金",
                "原文奖项名称": "Outstanding Student Scholarship",
                "发证机构": "星河大学",
                "原文发证机构": "Xinghe University",
            },
            ensure_ascii=False,
        ),
        review_status="auto_approved",
    )

    fields = material_to_bitable_fields(material)
    extracted_summary = json.loads(fields["提取字段JSON"])

    assert "翻译说明：英文/中英材料" in fields["OCR文本"]
    assert extracted_summary["翻译说明"].startswith("英文/中英材料")


def test_degree_material_mapping_includes_english_original_and_translation_note() -> None:
    material = MaterialSubmission(
        case_id=1,
        material_type="degree_certificate",
        file_name="Bachelor Degree.pdf",
        ocr_text="Certificate awarded to LIN Xiaoan by Xinghe University.",
        extracted_fields_json=json.dumps(
            {
                "姓名": "LIN Xiaoan",
                "学校": "星河大学",
                "原文学校": "Xinghe University",
                "学位类别": "学士学位",
                "原文学位类别": "Bachelor Degree",
                "证书名称": "学士学位证书",
                "原文证书名称": "Bachelor Degree Certificate",
            },
            ensure_ascii=False,
        ),
        review_status="auto_approved",
    )

    fields = material_to_bitable_fields(material)
    extracted_summary = json.loads(fields["提取字段JSON"])

    assert "原文学校：Xinghe University" in fields["OCR文本"]
    assert "翻译说明：英文/中英材料" in fields["OCR文本"]
    assert extracted_summary["原文证书名称"] == "Bachelor Degree Certificate"
