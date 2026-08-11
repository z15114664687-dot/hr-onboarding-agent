import json
from datetime import date, datetime

import pytest

from app.core.config import get_settings
from app.models.onboarding import CandidateCase, MaterialSubmission
from app.schemas.onboarding import MaterialCheck, MaterialReviewResult
from app.services import material_submission_flow


def test_text_inference_treats_award_proof_as_award_not_degree() -> None:
    actual_type = material_submission_flow._infer_non_resume_material_type(
        "星河大学优秀学生丙等奖学金证书 证书编号 2025 奖励证书",
        "优秀学生.pdf",
    )

    assert actual_type == "获奖证明"
    assert "award_certificate" in material_submission_flow._compatible_material_codes(actual_type)


def test_resume_award_context_prompts_missing_supporting_item() -> None:
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"获奖信息": ["优秀学生奖学金", "数学建模二等奖"]}, ensure_ascii=False),
            ocr_text="张三 简历 获奖信息：优秀学生奖学金；数学建模二等奖",
        )
    ]
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "奖项名称": "优秀学生奖学金"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "award_certificate",
        review,
        "星河大学 优秀学生奖学金 获奖证明",
    )

    assert adjusted.decision == "auto_rejected"
    assert "数学建模二等奖" in adjusted.message_to_candidate
    assert "继续上传" in adjusted.message_to_candidate


def test_invalid_award_certificate_is_not_rewritten_as_supplement() -> None:
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"获奖信息": ["优秀学生奖学金", "数学建模二等奖"]}, ensure_ascii=False),
            ocr_text="张三 简历 获奖信息：优秀学生奖学金；数学建模二等奖",
        )
    ]
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_rejected",
        extracted_fields={"材料类型匹配": True, "奖项名称": "优秀学生奖学金"},
        checks=[MaterialCheck(rule="发证机构或公章", status="fail", reason="未能识别到发证机构或公章")],
        message_to_candidate="材料未通过自动审核，请根据提示补充或重新提交。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "award_certificate",
        review,
        "优秀学生奖学金 获奖证明 张三",
    )

    assert adjusted.decision == "auto_rejected"
    assert adjusted.message_to_candidate == "材料未通过自动审核，请根据提示补充或重新提交。"
    assert not any(check.rule == "需继续补充材料" for check in adjusted.checks)
    material = MaterialSubmission(
        material_type="award_certificate",
        review_status=adjusted.decision,
        review_reason=material_submission_flow._review_reason(adjusted),
    )
    assert material_submission_flow._should_sync_material_record(material) is False


def test_award_certificate_requires_resume_context_first() -> None:
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "姓名": "张三", "奖项名称": "优秀学生奖学金", "发证机构": "星河大学"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "award_certificate",
        review,
        "星河大学 优秀学生奖学金 获奖证明 张三",
    )

    assert adjusted.decision == "auto_rejected"
    assert "还缺：求职简历" in adjusted.message_to_candidate
    assert "请先上传求职简历" in adjusted.message_to_candidate


def test_award_certificate_not_required_when_resume_has_no_supporting_items() -> None:
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"求学经历": [{"学校": "星河大学"}]}, ensure_ascii=False),
            ocr_text="张三 简历 教育经历：星河大学",
        )
    ]

    assert material_submission_flow.is_material_required_for_case(case, "award_certificate") is False


def test_resume_award_context_uses_fuzzy_award_name_match() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"获奖信息": ["优秀学生奖学金"]}, ensure_ascii=False),
            ocr_text="林晓安 简历 获奖信息：优秀学生奖学金",
        )
    ]
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "奖项名称": "优秀学生丙等奖学金"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "award_certificate",
        review,
        "星河大学优秀学生丙等奖学金证书 林晓安",
    )

    assert adjusted.decision == "auto_approved"
    assert not any(check.rule == "需继续补充材料" for check in adjusted.checks)


def test_resume_award_context_matches_school_prefixed_resume_item() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"获奖信息": ["星河大学优秀学生"]}, ensure_ascii=False),
            ocr_text="林晓安 简历 获奖信息：星河大学优秀学生",
        )
    ]
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "姓名": "林晓安", "奖项名称": "优秀学生", "发证机构": "星河大学"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "award_certificate",
        review,
        "优秀学生 获奖证明 林晓安 星河大学",
    )

    assert adjusted.decision == "auto_approved"
    assert not any(check.rule == "需继续补充材料" for check in adjusted.checks)


@pytest.mark.asyncio
async def test_ai_context_compare_can_resolve_strict_award_match(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"获奖信息": ["校长特别贡献奖"]}, ensure_ascii=False),
            ocr_text="林晓安 简历 获奖信息：校长特别贡献奖",
        )
    ]
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "姓名": "林晓安", "奖项名称": "卓越贡献奖", "发证机构": "星河大学"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    async def fake_compare(**kwargs):
        assert kwargs["requirements"] == ["校长特别贡献奖"]
        return {"matches": [{"label": "校长特别贡献奖", "matched": True, "confidence": "high", "reason": "同一奖项"}]}

    monkeypatch.setattr(material_submission_flow, "compare_material_requirements_with_ark", fake_compare)
    try:
        matched = await material_submission_flow._ai_requirement_context_matches(
            case,
            "award_certificate",
            review,
            "卓越贡献奖 获奖证明 林晓安 星河大学",
        )
    finally:
        get_settings.cache_clear()

    assert matched == {"校长特别贡献奖"}


def test_award_context_does_not_satisfy_missing_item_with_cross_file_generic_tokens() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps(
                {"获奖信息": ["优秀学生奖学金", "数学建模二等奖", "英语竞赛二等奖"]},
                ensure_ascii=False,
            ),
            ocr_text="林晓安 简历 获奖信息：优秀学生奖学金；数学建模二等奖；英语竞赛二等奖",
        ),
        MaterialSubmission(
            material_type="award_certificate",
            review_status="auto_rejected",
            review_reason="本次获奖证明已通过单份材料检查，但还缺：数学建模二等奖、英语竞赛二等奖。请继续上传缺少的文件。",
            extracted_fields_json=json.dumps({"奖项名称": "优秀学生奖学金"}, ensure_ascii=False),
            ocr_text="星河大学 优秀学生奖学金 获奖证明 林晓安",
        ),
    ]
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "奖项名称": "数学建模二等奖"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "award_certificate",
        review,
        "数学建模二等奖 获奖证明 林晓安",
    )

    assert adjusted.decision == "auto_rejected"
    assert "英语竞赛二等奖" in adjusted.message_to_candidate
    assert "优秀学生奖学金" not in adjusted.message_to_candidate
    assert "数学建模二等奖" not in adjusted.message_to_candidate


def test_reuploaded_resume_ignores_awards_uploaded_before_latest_resume() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            id=1,
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"获奖信息": ["优秀学生奖学金"]}, ensure_ascii=False),
            ocr_text="林晓安 简历 获奖信息：优秀学生奖学金",
            created_at=datetime(2026, 5, 20, 9, 0, 0),
        ),
        MaterialSubmission(
            id=2,
            material_type="award_certificate",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"奖项名称": "优秀学生奖学金"}, ensure_ascii=False),
            ocr_text="星河大学 优秀学生奖学金 获奖证明 林晓安",
            created_at=datetime(2026, 5, 20, 10, 0, 0),
        ),
        MaterialSubmission(
            id=3,
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps({"获奖信息": ["优秀学生奖学金", "数学建模二等奖"]}, ensure_ascii=False),
            ocr_text="林晓安 简历 获奖信息：优秀学生奖学金；数学建模二等奖",
            created_at=datetime(2026, 5, 21, 9, 0, 0),
        ),
    ]
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "奖项名称": "数学建模二等奖"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "award_certificate",
        review,
        "数学建模二等奖 获奖证明 林晓安",
    )

    assert adjusted.decision == "auto_rejected"
    assert "优秀学生奖学金" in adjusted.message_to_candidate
    assert "数学建模二等奖" not in adjusted.message_to_candidate


def test_degree_certificate_requires_resume_context_first() -> None:
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    review = MaterialReviewResult(
        material_type="degree_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "姓名": "张三", "学校": "星河大学", "检测到毕业证书": True, "检测到学位证书": True},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "degree_certificate",
        review,
        "星河大学 毕业证书 学位证书 张三",
    )

    assert adjusted.decision == "auto_rejected"
    assert "请先上传求职简历" in adjusted.message_to_candidate


def test_degree_certificate_context_prompts_missing_resume_school_certificate() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps(
                {"求学经历": [{"学校": "星河大学", "学历层次": "本科"}, {"学校": "海湾大学", "学历层次": "硕士"}]},
                ensure_ascii=False,
            ),
            ocr_text="林晓安 简历 教育经历：星河大学 本科；海湾大学 硕士",
        )
    ]
    review = MaterialReviewResult(
        material_type="degree_certificate",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "姓名": "林晓安", "学校": "星河大学", "学历层次": "本科", "检测到毕业证书": True, "检测到学位证书": True},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "degree_certificate",
        review,
        "星河大学 本科 毕业证书 学位证书 林晓安",
    )

    assert adjusted.decision == "auto_rejected"
    assert "海湾大学 硕士毕业证书" in adjusted.message_to_candidate
    assert "海湾大学 硕士学位证书" in adjusted.message_to_candidate


def test_invalid_degree_certificate_is_not_rewritten_as_supplement() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    case.materials = [
        MaterialSubmission(
            material_type="resume",
            review_status="auto_approved",
            extracted_fields_json=json.dumps(
                {"求学经历": [{"学校": "北桥大学", "学历层次": "本科"}, {"学校": "星河大学", "学历层次": "硕士"}]},
                ensure_ascii=False,
            ),
            ocr_text="林晓安 简历 教育经历：北桥大学 本科；星河大学 硕士",
        )
    ]
    review = MaterialReviewResult(
        material_type="degree_certificate",
        decision="auto_rejected",
        extracted_fields={
            "材料类型匹配": True,
            "姓名": "林晓安",
            "学校": "北桥大学",
            "检测到毕业证书": True,
            "检测到学位证书": True,
            "all_pages_upright": False,
        },
        checks=[
            MaterialCheck(
                rule="页面方向正向可读",
                status="fail",
                reason="存在倒置、侧向或无法正向阅读的页面：第2页侧向旋转90度",
            )
        ],
        message_to_candidate="材料未通过自动审核，请根据提示补充或重新提交。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "degree_certificate",
        review,
        "北桥大学 毕业证书 学位证书 林晓安 第2页侧向旋转90度",
    )

    assert adjusted.decision == "auto_rejected"
    assert adjusted.message_to_candidate == "材料未通过自动审核，请根据提示补充或重新提交。"
    assert not any(check.rule == "需继续补充材料" for check in adjusted.checks)
    material = MaterialSubmission(
        material_type="degree_certificate",
        review_status=adjusted.decision,
        review_reason=material_submission_flow._review_reason(adjusted),
    )
    assert material_submission_flow._should_sync_material_record(material) is False


@pytest.mark.asyncio
async def test_resume_fields_are_normalized_from_ocr_before_review() -> None:
    case = CandidateCase(id=1, candidate_name="林晓安", expected_onboard_date=date(2026, 6, 1))
    extracted = {
        "ocr_text": "林晓安\n教育经历\n星河大学 金融学\n获奖信息\n优秀学生丙等奖学金\n数学建模二等奖",
        "extracted_fields": {"姓名": "林晓安", "材料类型匹配": True},
        "quality_notes": [],
    }

    review, _ocr_text, _quality_notes = await material_submission_flow._review_extracted_material(
        case,
        "resume",
        "/tmp/resume.pdf",
        "个人简历.pdf",
        extracted,
        use_gemini_judgement=False,
    )

    assert review.decision == "auto_approved"
    assert review.extracted_fields["获奖信息"] == [{"奖项名称": "优秀学生丙等奖学金"}, {"奖项名称": "数学建模二等奖"}]
    assert review.extracted_fields["求学经历"] == [{"学校": "星河大学"}]


def test_supplement_review_reason_does_not_repeat_same_message() -> None:
    message = "本次获奖证明已通过单份材料检查，但还缺：数学建模二等奖。请继续上传缺少的文件。"
    review = MaterialReviewResult(
        material_type="award_certificate",
        decision="auto_rejected",
        extracted_fields={},
        checks=[MaterialCheck(rule="需继续补充材料", status="fail", reason=message)],
        message_to_candidate=message,
    )

    assert material_submission_flow._review_reason(review) == message


def test_employment_agreement_context_prompts_missing_recommendation_form() -> None:
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    review = MaterialReviewResult(
        material_type="employment_agreement",
        decision="auto_approved",
        extracted_fields={"材料类型匹配": True, "就业协议或推荐表": "就业协议书"},
        checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
        message_to_candidate="材料已通过自动审核。",
    )

    adjusted = material_submission_flow._apply_requirement_context_review(
        case,
        "employment_agreement",
        review,
        "普通高等学校毕业生就业协议书",
    )

    assert adjusted.decision == "auto_rejected"
    assert "就业推荐表" in adjusted.message_to_candidate


@pytest.mark.asyncio
async def test_degree_certificate_uses_text_precheck_for_xuexin_report(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()

    upload = tmp_path / "学位验证报告.pdf"
    upload.write_bytes(b"%PDF-1.4")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def remote_ocr_should_not_run(*args, **kwargs):
        raise AssertionError("readable xuexin PDF should be rejected before remote OCR")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", remote_ocr_should_not_run)
    monkeypatch.setattr(
        material_submission_flow,
        "_extract_pdf_text",
        lambda file_path, file_name: "学信网 中国高等教育学位在线验证报告",
    )
    try:
        review, ocr_text, quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "degree_certificate",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_rejected"
    assert review.review_source == "rules+local_text_precheck"
    assert "学信网" in ocr_text
    assert any(check.rule == "材料为学校发放证书" and check.status == "fail" for check in review.checks)
    assert any(check.rule == "毕业证书已提交" and check.status == "fail" for check in review.checks)
    assert any(check.rule == "学位证书已提交" and check.status == "fail" for check in review.checks)
    assert not any("500 Internal Server Error" in check.reason for check in review.checks)
    assert any("未调用视觉模型" in note for note in quality_notes)


@pytest.mark.asyncio
async def test_ocr_failure_without_pdf_fallback_uses_safe_manual_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()

    upload = tmp_path / "生活照.png"
    upload.write_bytes(b"image")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def failing_ocr(*args, **kwargs):
        raise RuntimeError("Ark OCR failed")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", failing_ocr)
    try:
        review, ocr_text, _quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "badge_photo",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "manual_review"
    assert ocr_text == ""
    assert review.checks[0].reason == "自动识别服务暂时不可用，已转人工复核"
    assert "500 Internal Server Error" not in review.checks[0].reason
    assert "500 Internal Server Error" not in review.message_to_candidate


@pytest.mark.asyncio
async def test_ocr_failure_uses_ark_fallback_for_images(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("ARK_MODEL", "doubao-seed-2-0-lite-260428")
    get_settings.cache_clear()

    upload = tmp_path / "工作证照片.png"
    upload.write_bytes(b"image" * 60_000)
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def fake_ark(*args, **kwargs):
        return {
            "ocr_text": "",
            "extracted_fields": {
                "单人照片": True,
                "五官清晰": True,
                "正式服装": True,
                "不合格照片类型": "",
                "clear_image": True,
            },
            "quality_notes": [],
        }

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", fake_ark)
    try:
        review, _ocr_text, quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "badge_photo",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_approved"
    assert review.review_source == "rules+ark_ocr"
    assert not quality_notes


@pytest.mark.asyncio
async def test_non_resume_uses_ark_ocr_and_skips_gemini_judgement(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    get_settings.cache_clear()

    upload = tmp_path / "工作证照片.png"
    upload.write_bytes(b"image" * 60_000)
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def fake_ark(*args, **kwargs):
        return {
            "ocr_text": "",
            "extracted_fields": {
                "单人照片": True,
                "五官清晰": True,
                "正式服装": True,
                "不合格照片类型": "",
                "clear_image": True,
            },
            "quality_notes": [],
        }

    async def gemini_judge_should_not_run(*args, **kwargs):
        raise AssertionError("material submission should not run Gemini judgement")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", fake_ark)
    monkeypatch.setattr(material_submission_flow, "judge_material_with_gemini", gemini_judge_should_not_run, raising=False)
    try:
        review, _ocr_text, quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "badge_photo",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_approved"
    assert review.review_source == "rules+ark_ocr"
    assert not quality_notes


@pytest.mark.asyncio
async def test_degree_certificate_xuexin_pdf_text_precheck_skips_remote_ocr(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()

    upload = tmp_path / "学位验证报告.pdf"
    upload.write_bytes(b"%PDF-1.4")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    async def remote_ocr_should_not_run(*args, **kwargs):
        raise AssertionError("readable xuexin PDF should be rejected before remote OCR")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", remote_ocr_should_not_run)
    monkeypatch.setattr(
        material_submission_flow,
        "_extract_pdf_text",
        lambda file_path, file_name: "学信网 中国高等教育学位在线验证报告",
    )
    try:
        review, ocr_text, quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "degree_certificate",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_rejected"
    assert review.review_source == "rules+local_text_precheck"
    assert "学信网" in ocr_text
    assert any("未调用视觉模型" in note for note in quality_notes)


@pytest.mark.asyncio
async def test_resume_prefers_ark_and_skips_gemini_judgement(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("ARK_MODEL", "doubao-seed-2-0-lite-260428")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    get_settings.cache_clear()

    upload = tmp_path / "个人简历.png"
    upload.write_bytes(b"image")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def fake_ark(*args, **kwargs):
        return {
            "ocr_text": "张三 求职简历 教育经历：某大学本科。工作经历：某公司实习。",
            "extracted_fields": {
                "实际材料类型": "求职简历",
                "材料类型匹配": True,
                "简历摘要": "包含教育经历和实习经历",
            },
            "quality_notes": [],
        }

    async def gemini_judge_should_not_run(*args, **kwargs):
        raise AssertionError("resume should not run Gemini judgement")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", fake_ark)
    monkeypatch.setattr(material_submission_flow, "judge_material_with_gemini", gemini_judge_should_not_run, raising=False)
    try:
        review, ocr_text, _quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "resume",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_approved"
    assert review.review_source == "rules+ark_ocr"
    assert "求职简历" in ocr_text


@pytest.mark.asyncio
async def test_resume_uses_local_text_precheck_for_readable_pdf(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()

    upload = tmp_path / "个人简历-张三.pdf"
    upload.write_bytes(b"%PDF-1.4")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def failing_ark(*args, **kwargs):
        raise RuntimeError("Ark OCR failed")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", failing_ark)
    monkeypatch.setattr(
        material_submission_flow,
        "_extract_pdf_text",
        lambda file_path, file_name: "张三 个人简历 教育经历：某大学本科。工作经历：某公司实习。",
    )
    try:
        review, ocr_text, quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "resume",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_approved"
    assert review.review_source == "rules+local_text_precheck"
    assert "个人简历" in ocr_text
    assert any("未调用视觉模型" in note for note in quality_notes)


@pytest.mark.asyncio
async def test_resume_ocr_failure_rejects_non_resume_pdf_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()

    upload = tmp_path / "学信网报告.pdf"
    upload.write_bytes(b"%PDF-1.4")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def failing_ark(*args, **kwargs):
        raise RuntimeError("Ark OCR failed")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", failing_ark)
    monkeypatch.setattr(
        material_submission_flow,
        "_extract_pdf_text",
        lambda file_path, file_name: "学信网 中国高等教育学历在线验证报告 在线验证码 ABCD",
    )
    try:
        review, ocr_text, quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "resume",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_rejected"
    assert review.review_source == "rules+local_text_precheck"
    assert "学信网" in ocr_text
    assert any(check.rule == "材料类型匹配" and check.status == "fail" for check in review.checks)
    assert any("非求职简历" in note for note in quality_notes)


@pytest.mark.asyncio
async def test_text_precheck_rejects_obvious_mismatched_material_when_ark_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "")
    monkeypatch.setenv("ARK_MODEL", "doubao-seed-2-0-lite-260428")
    get_settings.cache_clear()

    upload = tmp_path / "体检报告.pdf"
    upload.write_bytes(b"%PDF-1.4")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def remote_ocr_should_not_run(*args, **kwargs):
        raise AssertionError("obvious mismatched text PDF should not call remote OCR")

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", remote_ocr_should_not_run)
    monkeypatch.setattr(
        material_submission_flow,
        "_extract_pdf_text",
        lambda file_path, file_name: "体检报告 总检结论 合格 体检中心",
    )
    try:
        review, ocr_text, quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "bank_card",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_rejected"
    assert review.review_source == "rules+local_text_precheck"
    assert "体检报告" in ocr_text
    assert "当前文件看起来是体检报告" in review.message_to_candidate
    assert any("材料类型不匹配" in note for note in quality_notes)


@pytest.mark.asyncio
async def test_matching_text_pdf_prefers_ark_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("ARK_MODEL", "doubao-seed-2-0-lite-260428")
    get_settings.cache_clear()

    upload = tmp_path / "体检报告.pdf"
    upload.write_bytes(b"%PDF-1.4")
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
    called = {"ark": False}

    async def fake_ark(*args, **kwargs):
        called["ark"] = True
        return {
            "ocr_text": "姓名：张三 体检日期：2026-05-01 体检机构：某体检中心 总检结论：合格",
            "extracted_fields": {
                "材料类型匹配": True,
                "姓名": "张三",
                "体检日期": "2026-05-01",
                "体检机构": "某体检中心",
                "总检结论": "合格",
                "有结论页": True,
                "color_scan": True,
                "clear_image": True,
                "complete_document": True,
                "original_scan": True,
            },
            "quality_notes": [],
        }

    monkeypatch.setattr(material_submission_flow, "_extract_material_with_ark_file", fake_ark)
    monkeypatch.setattr(
        material_submission_flow,
        "_extract_pdf_text",
        lambda file_path, file_name: "体检报告 总检结论 合格 体检中心",
    )
    try:
        review, _ocr_text, _quality_notes = await material_submission_flow._review_uploaded_file(
            case,
            "medical_report",
            str(upload),
            upload.name,
        )
    finally:
        get_settings.cache_clear()

    assert called["ark"] is True
    assert review.decision == "auto_approved"
    assert review.review_source == "rules+ark_ocr"


@pytest.mark.asyncio
async def test_upload_review_timeout_records_manual_review(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))

    async def slow_review(*args, **kwargs):
        import asyncio

        await asyncio.sleep(0.02)

    monkeypatch.setattr(material_submission_flow, "MATERIAL_REVIEW_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(material_submission_flow, "_review_uploaded_file", slow_review)
    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "")
    monkeypatch.setenv("FEISHU_CANDIDATE_TABLE_ID", "")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "")
    monkeypatch.setenv("FEISHU_APP_ID", "")
    monkeypatch.setenv("FEISHU_APP_SECRET", "")
    get_settings.cache_clear()

    try:
        with session_factory() as db:
            db.add(case)
            db.commit()
            result = await material_submission_flow.review_and_record_material_submission(
                db=db,
                case=case,
                material_type="hukou_material",
                file_path=str(tmp_path / "户籍材料.jpg"),
                file_name="户籍材料.jpg",
            )
    finally:
        get_settings.cache_clear()

    assert result["review"].decision == "manual_review"
    assert result["material"].review_reason == "材料已收到，自动审核耗时较长，已转由 HR 人工审核。 需要人工确认：自动审核超时，已转人工复核"


@pytest.mark.asyncio
async def test_material_submission_can_skip_individual_candidate_notify(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    case = CandidateCase(
        id=1,
        candidate_name="张三",
        feishu_open_id="ou_candidate",
        expected_onboard_date=date(2026, 6, 1),
    )

    async def fake_review(*args, **kwargs):
        return (
            MaterialReviewResult(
                material_type="award_certificate",
                decision="auto_approved",
                extracted_fields={},
                checks=[MaterialCheck(rule="材料类型匹配", status="pass", reason="上传材料类型匹配")],
                message_to_candidate="材料已通过自动审核。",
            ),
            "",
            [],
        )

    async def fail_notify(*args, **kwargs):
        raise AssertionError("batch uploads should not send per-file candidate notifications")

    monkeypatch.setattr(material_submission_flow, "_review_uploaded_file", fake_review)
    monkeypatch.setattr(material_submission_flow, "notify_candidate_material_result", fail_notify)
    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "")
    monkeypatch.setenv("FEISHU_CANDIDATE_TABLE_ID", "")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "")
    get_settings.cache_clear()

    try:
        with session_factory() as db:
            db.add(case)
            db.commit()
            result = await material_submission_flow.review_and_record_material_submission(
                db=db,
                case=case,
                material_type="award_certificate",
                file_path=str(tmp_path / "获奖证明.pdf"),
                file_name="获奖证明.pdf",
                notify_candidate=False,
            )
    finally:
        get_settings.cache_clear()

    assert result["review"].decision == "auto_approved"
    assert result["candidate_notify"]["status"] == "skipped"
    assert result["candidate_notify"]["reason"] == "batch summary notification pending"


@pytest.mark.asyncio
async def test_material_bitable_sync_replaces_previous_same_material_record(monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    deleted: list[str] = []

    async def fake_delete_record(_app_token, _table_id, record_id):
        deleted.append(record_id)
        return {"data": {}}

    async def fake_create_record(_app_token, _table_id, fields):
        assert fields["候选人ID"] == "1"
        assert fields["候选人姓名"] == "张三"
        assert fields["审核状态"] == "已通过"
        return {"data": {"record": {"record_id": "rec_new"}}}

    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "app_token")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "table=tble290As4J788Ll&view=vewSvnr8md")
    monkeypatch.setattr(material_submission_flow, "delete_record", fake_delete_record)
    monkeypatch.setattr(material_submission_flow, "create_record", fake_create_record)
    get_settings.cache_clear()

    try:
        with session_factory() as db:
            case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
            old_material = MaterialSubmission(
                case_id=1,
                material_type="resume",
                file_name="旧简历.pdf",
                review_status="manual_review",
                feishu_bitable_record_id="rec_old",
            )
            new_material = MaterialSubmission(
                case_id=1,
                material_type="resume",
                file_name="新简历.pdf",
                review_status="auto_approved",
            )
            db.add_all([case, old_material, new_material])
            db.commit()
            db.refresh(new_material)

            result = await material_submission_flow.sync_material_to_bitable(new_material, db)
            db.refresh(old_material)
            db.refresh(new_material)
    finally:
        get_settings.cache_clear()

    assert result["status"] == "success"
    assert deleted == ["rec_old"]
    assert old_material.feishu_bitable_record_id is None
    assert new_material.feishu_bitable_record_id == "rec_new"


@pytest.mark.asyncio
async def test_material_bitable_sync_keeps_multiple_supporting_records(monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    deleted: list[str] = []

    async def fake_delete_record(_app_token, _table_id, record_id):
        deleted.append(record_id)
        return {"data": {}}

    async def fake_create_record(_app_token, _table_id, fields):
        assert fields["审核状态"] == "已通过"
        return {"data": {"record": {"record_id": "rec_new"}}}

    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "app_token")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "tble290As4J788Ll")
    monkeypatch.setattr(material_submission_flow, "delete_record", fake_delete_record)
    monkeypatch.setattr(material_submission_flow, "create_record", fake_create_record)
    get_settings.cache_clear()

    try:
        with session_factory() as db:
            case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
            old_material = MaterialSubmission(
                case_id=1,
                material_type="award_certificate",
                file_name="优秀学生奖学金.pdf",
                review_status="auto_rejected",
                review_reason="本次获奖证明已通过单份材料检查，但还缺：数学建模二等奖。请继续上传缺少的文件。",
                feishu_bitable_record_id="rec_old",
            )
            new_material = MaterialSubmission(
                case_id=1,
                material_type="award_certificate",
                file_name="数学建模二等奖.pdf",
                review_status="auto_approved",
            )
            db.add_all([case, old_material, new_material])
            db.commit()
            db.refresh(new_material)

            result = await material_submission_flow.sync_material_to_bitable(new_material, db)
            db.refresh(old_material)
            db.refresh(new_material)
    finally:
        get_settings.cache_clear()

    assert result["status"] == "success"
    assert deleted == []
    assert old_material.feishu_bitable_record_id == "rec_old"
    assert new_material.feishu_bitable_record_id == "rec_new"


@pytest.mark.asyncio
async def test_material_bitable_sync_writes_supplement_records(monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    created: list[dict] = []

    async def fake_create_record(_app_token, _table_id, fields):
        created.append(fields)
        return {"data": {"record": {"record_id": "rec_supplement"}}}

    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "app_token")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "tble290As4J788Ll")
    monkeypatch.setattr(material_submission_flow, "create_record", fake_create_record)
    get_settings.cache_clear()

    try:
        with session_factory() as db:
            case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
            material = MaterialSubmission(
                case_id=1,
                material_type="award_certificate",
                file_name="优秀学生奖学金.pdf",
                review_status="auto_rejected",
                review_reason="本次获奖证明已通过单份材料检查，但还缺：求职简历。请先上传求职简历，再继续上传对应证明材料。",
            )
            db.add_all([case, material])
            db.commit()
            db.refresh(material)

            result = await material_submission_flow.sync_material_to_bitable(material, db)
            db.refresh(material)
    finally:
        get_settings.cache_clear()

    assert result["status"] == "success"
    assert created[0]["审核状态"] == "需补充"
    assert material.feishu_bitable_record_id == "rec_supplement"


@pytest.mark.asyncio
async def test_material_bitable_sync_skips_rejected_and_deletes_current_record(monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    deleted: list[str] = []

    async def fake_delete_record(_app_token, _table_id, record_id):
        deleted.append(record_id)
        return {"data": {}}

    async def create_record_should_not_run(*_args, **_kwargs):
        raise AssertionError("rejected material should not be created in material table")

    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "app_token")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "tble290As4J788Ll")
    monkeypatch.setattr(material_submission_flow, "delete_record", fake_delete_record)
    monkeypatch.setattr(material_submission_flow, "create_record", create_record_should_not_run)
    get_settings.cache_clear()

    try:
        with session_factory() as db:
            case = CandidateCase(id=1, candidate_name="张三", expected_onboard_date=date(2026, 6, 1))
            material = MaterialSubmission(
                case_id=1,
                material_type="resume",
                file_name="错误文件.pdf",
                review_status="auto_rejected",
                feishu_bitable_record_id="rec_rejected",
            )
            db.add_all([case, material])
            db.commit()
            db.refresh(material)

            result = await material_submission_flow.sync_material_to_bitable(material, db)
            db.refresh(material)
    finally:
        get_settings.cache_clear()

    assert result["status"] == "skipped"
    assert deleted == ["rec_rejected"]
    assert material.feishu_bitable_record_id is None
