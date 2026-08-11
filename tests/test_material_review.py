from datetime import date

import pytest

from app.services.material_review import review_material

SYNTHETIC_VALID_ID = "00000020000101001" + "3"  # Valid checksum; impossible region code.


def test_id_card_review_valid_id_number() -> None:
    result = review_material(
        material_type="id_card",
        candidate_name="张三",
        expected_onboard_date=date(2026, 6, 1),
        ocr_text="",
        mock_extracted_fields={
            "姓名": "张三",
            "身份证号": SYNTHETIC_VALID_ID,
            "有效期限": "2030-12-31",
            "missing_back_side": False,
        },
    )

    assert result.decision == "auto_approved"
    assert any(check.rule == "身份证号格式和校验位" and check.status == "pass" for check in result.checks)


def test_resignation_certificate_missing_stamp_rejected() -> None:
    result = review_material(
        material_type="resignation_certificate",
        candidate_name="李四",
        expected_onboard_date=date(2026, 6, 1),
        ocr_text="",
        mock_extracted_fields={
            "姓名": "李四",
            "原单位名称": "前公司",
            "离职日期": "2026-05-20",
            "盖章识别结果": False,
        },
    )

    assert result.decision == "auto_rejected"
    assert any(check.rule == "检测到盖章" and check.status == "fail" for check in result.checks)


def test_medical_report_review_keyword_manual_review() -> None:
    result = review_material(
        material_type="medical_report",
        candidate_name="王五",
        expected_onboard_date=date(2026, 6, 1),
        ocr_text="总检结论：建议复查",
        mock_extracted_fields={
            "姓名": "王五",
            "体检日期": "2026-05-15",
            "体检机构": "某体检中心",
            "总检结论": "建议复查",
            "有结论页": True,
        },
    )

    assert result.decision == "manual_review"
    assert any(check.rule == "体检风险关键词" and check.status == "manual_review" for check in result.checks)


def test_id_card_review_rejects_bad_upload_standard() -> None:
    result = review_material(
        material_type="id_card",
        candidate_name="张三",
        expected_onboard_date=date(2026, 6, 1),
        ocr_text="",
        mock_extracted_fields={
            "姓名": "张三",
            "身份证号": SYNTHETIC_VALID_ID,
            "有效期限": "2030-12-31",
            "missing_back_side": False,
            "file_format": "jpg",
            "color_scan": False,
            "clear_image": True,
            "complete_document": True,
            "original_scan": True,
            "front_back_same_page": False,
        },
    )

    assert result.decision == "auto_rejected"
    assert any(check.rule == "彩色扫描/电子原件" and check.status == "fail" for check in result.checks)
    assert any(check.rule == "证件正反面同页" and check.status == "fail" for check in result.checks)


def test_employment_manual_requires_cover_photo_and_stamp_pages() -> None:
    result = review_material(
        material_type="employment_manual",
        candidate_name="赵六",
        expected_onboard_date=date(2026, 6, 1),
        ocr_text="",
        mock_extracted_fields={
            "封面": True,
            "个人照片页": False,
            "盖章页": True,
            "file_format": "pdf",
            "clear_image": True,
            "complete_document": False,
        },
    )

    assert result.decision == "auto_rejected"
    assert any(check.rule == "必填字段：个人照片页" and check.status == "fail" for check in result.checks)


def test_multipage_material_rejects_inconsistent_orientation() -> None:
    result = review_material(
        material_type="hukou_material",
        candidate_name="赵六",
        expected_onboard_date=date(2026, 6, 1),
        ocr_text="",
        mock_extracted_fields={
            "户口本首页": True,
            "个人常住人口登记卡页": True,
            "登记事项变更页": True,
            "file_format": "pdf",
            "color_scan": True,
            "clear_image": True,
            "complete_document": True,
            "original_scan": True,
            "all_pages_upright": True,
            "image_orientation_consistent": False,
            "orientation_issue": "第2页倒置，与第1页方向不一致",
        },
    )

    assert result.decision == "auto_rejected"
    assert any(
        check.rule == "文件内图像方向一致" and check.status == "fail" and "第2页倒置" in check.reason
        for check in result.checks
    )


def test_degree_certificate_requires_graduation_and_degree_certificates() -> None:
    result = review_material(
        material_type="degree_certificate",
        candidate_name="张三",
        ocr_text="普通高等学校毕业证书",
        mock_extracted_fields={
            "姓名": "张三",
            "学校": "某大学",
            "证书类型": "毕业证书",
            "证书编号": "ABC123",
            "检测到毕业证书": True,
            "检测到学位证书": False,
            "疑似学信网报告": False,
        },
    )

    assert result.decision == "auto_rejected"
    assert any(check.rule == "学位证书已提交" and check.status == "fail" for check in result.checks)


def test_resume_review_extracts_structured_resume_info() -> None:
    result = review_material(
        material_type="resume",
        candidate_name="张三",
        ocr_text="",
        mock_extracted_fields={
            "姓名": "张三",
            "求学经历": ["某大学 本科"],
            "工作经历": ["某公司 实习"],
            "职称资格": ["岗位资格资格"],
            "获奖信息": ["一等奖学金"],
            "材料类型匹配": True,
        },
    )

    assert result.decision == "auto_approved"
    assert any(check.rule == "简历经历提取" and check.status == "pass" for check in result.checks)


def test_resume_review_accepts_readable_resume_text_without_structured_experience_fields() -> None:
    result = review_material(
        material_type="resume",
        candidate_name="张三",
        ocr_text="张三 求职简历 教育经历：某大学本科。工作经历：某公司实习。",
        mock_extracted_fields={
            "实际材料类型": "求职简历",
            "材料类型匹配": True,
            "简历摘要": "包含教育经历和实习经历",
        },
    )

    assert result.decision == "auto_approved"
    assert any(check.rule == "姓名提取" and check.status == "pass" for check in result.checks)
    assert any(check.rule == "简历经历提取" and check.status == "pass" for check in result.checks)


def test_bank_card_accepts_icbc_without_cardholder_name() -> None:
    result = review_material(
        material_type="bank_card",
        candidate_name="李四",
        ocr_text="中国工商银行 ICBC 6222 ****",
        mock_extracted_fields={
            "银行名称": "中国工商银行",
            "银行卡号": "6222 **** **** **** ***",
            "是否工商银行卡": True,
            "clear_image": True,
            "complete_document": True,
        },
    )

    assert result.decision == "auto_approved"
    assert not any(check.rule == "姓名一致" for check in result.checks)
    assert any(check.rule == "工商银行卡" and check.status == "pass" for check in result.checks)


def test_award_certificate_accepts_school_or_stamp_issuer_aliases() -> None:
    result = review_material(
        material_type="award_certificate",
        candidate_name="林晓安",
        ocr_text="星河大学 优秀学生丙等奖学金 林晓安 星河大学学生工作部",
        mock_extracted_fields={
            "姓名": "林晓安",
            "证书名称": "优秀学生丙等奖学金",
            "学校": "星河大学",
            "材料类型匹配": True,
            "clear_image": True,
            "complete_document": True,
        },
    )

    assert result.decision == "auto_approved"
    assert not [check for check in result.checks if check.status != "pass"]


def test_award_certificate_accepts_chinese_name_with_pinyin_annotation() -> None:
    result = review_material(
        material_type="award_certificate",
        candidate_name="林晓安",
        ocr_text="Certificate of Outstanding Student Scholarship LIN Xiaoan 林晓安 Xinghe University",
        mock_extracted_fields={
            "姓名": "林晓安（LIN Xiaoan）",
            "奖项名称": "优秀学生奖学金",
            "发证机构": "星河大学",
            "材料类型匹配": True,
            "clear_image": True,
            "complete_document": True,
        },
    )

    assert result.decision == "auto_approved"
    assert any(check.rule == "姓名一致" and check.status == "pass" for check in result.checks)


def test_award_certificate_accepts_pinyin_name_when_available() -> None:
    pytest.importorskip("pypinyin")
    result = review_material(
        material_type="award_certificate",
        candidate_name="林晓安",
        ocr_text="Certificate of Outstanding Student Scholarship LIN Xiaoan Xinghe University",
        mock_extracted_fields={
            "姓名": "LIN Xiaoan",
            "奖项名称": "优秀学生奖学金",
            "发证机构": "星河大学",
            "材料类型匹配": True,
            "clear_image": True,
            "complete_document": True,
        },
    )

    assert result.decision == "auto_approved"
    assert any(check.rule == "姓名一致" and check.status == "pass" for check in result.checks)


def test_badge_photo_uses_visual_requirements() -> None:
    result = review_material(
        material_type="badge_photo",
        candidate_name="王五",
        ocr_text="",
        mock_extracted_fields={
            "单人照片": True,
            "五官清晰": True,
            "正式服装": True,
            "不合格照片类型": "",
            "file_size_kb": 260,
            "clear_image": True,
        },
    )

    assert result.decision == "auto_approved"
    assert any(check.rule == "单人工作证照片" and check.status == "pass" for check in result.checks)
