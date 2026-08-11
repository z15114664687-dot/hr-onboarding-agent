from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from app.core.untrusted_content import find_untrusted_content_risks
from app.schemas.onboarding import MaterialCheck, MaterialReviewResult
from app.services.material_standards import get_material_standard

RISK_MEDICAL_KEYWORDS = ("建议复查", "不合格", "异常", "职业禁忌")


def review_material(
    *,
    material_type: str,
    candidate_name: str,
    ocr_text: str,
    expected_onboard_date: date | None = None,
    mock_extracted_fields: dict[str, Any] | None = None,
) -> MaterialReviewResult:
    """Run rule-based material review using OCR text and optional extracted fields."""

    extracted = mock_extracted_fields or _extract_fields_from_text(ocr_text)
    reviewers = {
        "badge_photo": _review_badge_photo,
        "id_card": _review_id_card,
        "resume": _review_resume,
        "degree_certificate": _review_degree_certificate,
        "xuexin_report": _review_xuexin_report,
        "overseas_degree_certification": _review_overseas_degree_certification,
        "hukou_material": _review_generic_standard_material,
        "bank_card": _review_bank_card,
        "securities_statement": _review_generic_standard_material,
        "labor_termination_form": _review_generic_standard_material,
        "employment_manual": _review_generic_standard_material,
        "employment_agreement": _review_generic_standard_material,
        "professional_certificate": _review_generic_standard_material,
        "award_certificate": _review_award_certificate,
        "resignation_certificate": _review_resignation_certificate,
        "medical_report": _review_medical_report,
    }
    reviewer = reviewers.get(material_type)
    if not reviewer:
        return MaterialReviewResult(
            material_type=material_type,
            decision="manual_review",
            extracted_fields=extracted,
            checks=[MaterialCheck(rule="材料类型支持", status="manual_review", reason="暂未配置该材料规则")],
            message_to_candidate="材料已收到，将由 HR 人工审核。",
        )
    checks = _upload_standard_checks(material_type, extracted)
    checks.extend(reviewer(candidate_name, extracted, ocr_text, expected_onboard_date, material_type))
    checks.extend(
        MaterialCheck(rule=f"untrusted_document:{finding.code}", status="manual_review", reason=finding.reason)
        for finding in find_untrusted_content_risks(ocr_text, extracted)
    )
    decision = _decision_from_checks(checks)
    return MaterialReviewResult(
        material_type=material_type,
        decision=decision,
        extracted_fields=extracted,
        checks=checks,
        message_to_candidate=_candidate_message(decision),
    )


def _review_id_card(
    candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "id_card",
) -> list[MaterialCheck]:
    return [
        _required("姓名", fields.get("姓名")),
        _required("身份证号", fields.get("身份证号")),
        _required("有效期限", fields.get("有效期限")),
        _name_check(candidate_name, fields.get("姓名")),
        _id_number_check(fields.get("身份证号")),
        MaterialCheck(
            rule="身份证正反面完整",
            status="fail" if fields.get("missing_back_side") else "pass",
            reason="缺少身份证背面" if fields.get("missing_back_side") else "已检测到正反面占位字段",
        ),
    ]


def _review_xuexin_report(
    candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "xuexin_report",
) -> list[MaterialCheck]:
    checks = [
        _required("姓名", fields.get("姓名")),
        _required("学校", fields.get("学校")),
        _required("专业", fields.get("专业")),
        _required("学历层次或学位类别", fields.get("学历层次") or fields.get("学位类别")),
        _required("证书编号或在线验证码", fields.get("证书编号") or fields.get("在线验证码")),
        _required("报告有效期", fields.get("报告有效期")),
        _name_check(candidate_name, fields.get("姓名")),
        MaterialCheck(
            rule="在线验证码存在",
            status="pass" if fields.get("在线验证码") else "fail",
            reason="已提取在线验证码" if fields.get("在线验证码") else "未提取在线验证码",
        ),
    ]
    checks.append(_date_not_expired("报告未过期", fields.get("报告有效期")))
    return checks


def _review_overseas_degree_certification(
    candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "overseas_degree_certification",
) -> list[MaterialCheck]:
    return [
        _required("姓名", fields.get("姓名")),
        _required("认证书编号", fields.get("认证书编号")),
        _required("院校", fields.get("院校")),
        _required("国别/地区", fields.get("国别/地区")),
        _required("认证结论", fields.get("认证结论")),
        _name_check(candidate_name, fields.get("姓名")),
        MaterialCheck(
            rule="认证书编号存在",
            status="pass" if fields.get("认证书编号") else "fail",
            reason="已提取认证书编号" if fields.get("认证书编号") else "未提取认证书编号",
        ),
    ]


def _review_resignation_certificate(
    candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    expected_onboard_date: date | None,
    _material_type: str = "resignation_certificate",
) -> list[MaterialCheck]:
    checks = [
        _required("姓名", fields.get("姓名")),
        _required("原单位名称", fields.get("原单位名称")),
        _required("离职/解除/终止日期", fields.get("离职日期") or fields.get("解除日期") or fields.get("终止日期")),
        _required("盖章识别结果", fields.get("盖章识别结果")),
        _name_check(candidate_name, fields.get("姓名")),
    ]
    leave_date = _parse_date(fields.get("离职日期") or fields.get("解除日期") or fields.get("终止日期"))
    checks.append(
        MaterialCheck(
            rule="离职日期早于预计入职日期",
            status="pass" if leave_date and expected_onboard_date and leave_date < expected_onboard_date else "manual_review",
            reason="日期符合要求" if leave_date and expected_onboard_date and leave_date < expected_onboard_date else "需人工确认离职日期",
        )
    )
    has_stamp = bool(fields.get("盖章识别结果") in (True, "true", "是", "有", "detected"))
    checks.append(
        MaterialCheck(
            rule="检测到盖章",
            status="pass" if has_stamp else "fail",
            reason="已检测到盖章" if has_stamp else "未检测到盖章",
        )
    )
    return checks


def _review_medical_report(
    candidate_name: str,
    fields: dict[str, Any],
    ocr_text: str,
    expected_onboard_date: date | None,
    _material_type: str = "medical_report",
) -> list[MaterialCheck]:
    checks = [
        _required("姓名", fields.get("姓名")),
        _required("体检日期", fields.get("体检日期")),
        _required("体检机构", fields.get("体检机构")),
        _required("总检结论", fields.get("总检结论")),
        _name_check(candidate_name, fields.get("姓名")),
        MaterialCheck(
            rule="有结论页",
            status="pass" if fields.get("有结论页", True) else "manual_review",
            reason="已检测到结论页" if fields.get("有结论页", True) else "未确认结论页",
        ),
    ]
    medical_date = _parse_date(fields.get("体检日期"))
    valid = bool(medical_date and expected_onboard_date and (expected_onboard_date - medical_date).days <= 90)
    checks.append(
        MaterialCheck(
            rule="体检日期在有效期内",
            status="pass" if valid else "manual_review",
            reason="体检日期在 90 天有效期内" if valid else "需人工确认体检日期有效性",
        )
    )
    has_risk_keyword = any(keyword in ocr_text or keyword in str(fields.get("总检结论", "")) for keyword in RISK_MEDICAL_KEYWORDS)
    checks.append(
        MaterialCheck(
            rule="体检风险关键词",
            status="manual_review" if has_risk_keyword else "pass",
            reason="出现复查/异常等关键词" if has_risk_keyword else "未发现高风险关键词",
        )
    )
    return checks


def _review_degree_certificate(
    candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "degree_certificate",
) -> list[MaterialCheck]:
    detected_types = _detected_types(fields)
    has_graduation = _truthy(fields.get("检测到毕业证书")) or any("毕业证" in item for item in detected_types)
    has_degree = _truthy(fields.get("检测到学位证书")) or any("学位证" in item for item in detected_types)
    is_xuexin_report = _truthy(fields.get("疑似学信网报告")) or "学信网" in _ocr_text or "在线验证报告" in _ocr_text
    return [
        _required("姓名", fields.get("姓名")),
        _required("学校", fields.get("学校")),
        _required("证书类型", fields.get("证书类型") or fields.get("学历层次") or fields.get("学位类别")),
        _required("证书编号", fields.get("证书编号")),
        _name_check(candidate_name, fields.get("姓名")),
        MaterialCheck(
            rule="材料为学校发放证书",
            status="fail" if is_xuexin_report else "pass",
            reason="检测到学信网/在线验证报告，不能替代学校发放的毕业证书和学位证书" if is_xuexin_report else "未检测到学信网报告替代",
        ),
        MaterialCheck(
            rule="毕业证书已提交",
            status="pass" if has_graduation else "fail",
            reason="已检测到毕业证书" if has_graduation else "未检测到学校发放的毕业证书",
        ),
        MaterialCheck(
            rule="学位证书已提交",
            status="pass" if has_degree else "fail",
            reason="已检测到学位证书" if has_degree else "未检测到学校发放的学位证书",
        ),
    ]


def _review_resume(
    candidate_name: str,
    fields: dict[str, Any],
    ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "resume",
) -> list[MaterialCheck]:
    core_keys = ("求学经历", "工作经历", "实习经历", "职称资格", "获奖信息")
    has_core_experience = any(_has_value(fields.get(key)) for key in core_keys)
    has_resume_text = _has_value(ocr_text) or _has_value(fields.get("简历摘要"))
    actual_name = fields.get("姓名")

    if _has_value(actual_name):
        name_check = _name_check(candidate_name, actual_name)
    else:
        name_check = MaterialCheck(
            rule="姓名提取",
            status="pass" if has_resume_text else "manual_review",
            reason="未结构化提取姓名，已保留简历正文供后续核对" if has_resume_text else "未能识别姓名和简历正文",
        )

    return [
        name_check,
        MaterialCheck(
            rule="简历正文可识别",
            status="pass" if has_resume_text or has_core_experience else "manual_review",
            reason="已识别简历正文或摘要" if has_resume_text or has_core_experience else "未能识别可用简历正文",
        ),
        MaterialCheck(
            rule="简历经历提取",
            status="pass" if has_core_experience or has_resume_text else "manual_review",
            reason="已提取结构化经历信息" if has_core_experience else "未完整拆分经历字段，已保留简历正文供后续核对",
        ),
    ]


def _review_badge_photo(
    _candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "badge_photo",
) -> list[MaterialCheck]:
    checks = [
        MaterialCheck(
            rule="单人工作证照片",
            status=_visual_bool_status(fields.get("单人照片")),
            reason=_visual_bool_reason(fields.get("单人照片"), "已检测到单人照片", "未检测到单人照片或疑似合影"),
        ),
        MaterialCheck(
            rule="五官清晰",
            status=_visual_bool_status(fields.get("五官清晰") if "五官清晰" in fields else fields.get("人像清晰")),
            reason=_visual_bool_reason(
                fields.get("五官清晰") if "五官清晰" in fields else fields.get("人像清晰"),
                "五官清晰可辨",
                "五官不清晰或被遮挡",
            ),
        ),
    ]
    file_size = _number(fields.get("file_size_kb"))
    if file_size is not None:
        checks.append(
            MaterialCheck(
                rule="照片大小大于200K",
                status="pass" if file_size >= 200 else "fail",
                reason=f"照片大小约 {file_size:g}K" if file_size >= 200 else f"照片大小约 {file_size:g}K，低于 200K",
            )
        )
    disallowed_type = str(fields.get("不合格照片类型") or "").strip()
    checks.append(
        MaterialCheck(
            rule="照片类型符合要求",
            status="fail" if disallowed_type else "pass",
            reason=f"疑似{disallowed_type}，不符合工作证照片要求" if disallowed_type else "未发现自拍、证件寸照、合影、墨镜照等问题",
        )
    )
    if "正式服装" in fields:
        checks.append(
            MaterialCheck(
                rule="服装接近工作证照片要求",
                status=_visual_bool_status(fields.get("正式服装")),
                reason=_visual_bool_reason(fields.get("正式服装"), "服装接近工作证照片要求", "服装不符合工作证照片建议"),
            )
        )
    return checks


def _review_bank_card(
    _candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "bank_card",
) -> list[MaterialCheck]:
    bank_name = str(fields.get("银行名称") or "")
    is_icbc_value = fields.get("是否工商银行卡")
    is_icbc = _truthy(is_icbc_value) or "工商" in bank_name or "ICBC" in bank_name.upper()
    non_icbc = _falsey(is_icbc_value) or (bool(bank_name) and not is_icbc)
    return [
        _required("银行名称", fields.get("银行名称")),
        _required("银行卡号", fields.get("银行卡号")),
        MaterialCheck(
            rule="工商银行卡",
            status="pass" if is_icbc else "fail" if non_icbc else "manual_review",
            reason="已识别为中国工商银行/ICBC" if is_icbc else f"识别到的银行为 {bank_name or '未知'}，不是工商银行" if non_icbc else "无法确认是否为工商银行",
        ),
    ]


def _review_generic_standard_material(
    candidate_name: str,
    fields: dict[str, Any],
    _ocr_text: str,
    _expected_onboard_date: date | None,
    material_type: str,
) -> list[MaterialCheck]:
    standard = get_material_standard(material_type)
    if not standard:
        return [MaterialCheck(rule="材料标准配置", status="manual_review", reason="暂无该材料的专门标准")]

    checks = [_required(field_name, _field_value(fields, field_name)) for field_name in standard.required_fields]
    if fields.get("姓名"):
        checks.append(_name_check(candidate_name, fields.get("姓名")))
    return checks


def _review_award_certificate(
    candidate_name: str,
    fields: dict[str, Any],
    ocr_text: str,
    _expected_onboard_date: date | None,
    _material_type: str = "award_certificate",
) -> list[MaterialCheck]:
    actual_name = fields.get("姓名") or (candidate_name if candidate_name and candidate_name in ocr_text else None)
    award_name = _first_field(
        fields,
        ("奖项名称", "获奖名称", "奖励名称", "荣誉名称", "证书名称", "名称", "奖项"),
    ) or _infer_award_name(ocr_text)
    issuer = _first_field(
        fields,
        ("发证机构", "颁发机构", "颁发单位", "授予单位", "学校", "院校", "盖章单位", "公章文字", "盖章文字", "落款"),
    ) or _infer_award_issuer(ocr_text)
    stamp_value = _first_field(fields, ("盖章识别结果", "公章文字", "盖章文字", "盖章单位"))
    has_issuer_or_stamp = _has_value(issuer) or _truthy(stamp_value)
    name_check = _name_check(candidate_name, actual_name) if _has_value(actual_name) else _required("姓名", actual_name)
    return [
        name_check,
        MaterialCheck(
            rule="必填字段：奖项名称",
            status="pass" if _has_value(award_name) else "fail",
            reason="已提取" if _has_value(award_name) else "缺少该字段",
        ),
        MaterialCheck(
            rule="发证机构或公章",
            status="pass" if has_issuer_or_stamp else "fail",
            reason="已识别发证机构或公章" if has_issuer_or_stamp else "未能识别到发证机构或公章",
        ),
    ]


def _upload_standard_checks(material_type: str, fields: dict[str, Any]) -> list[MaterialCheck]:
    standard = get_material_standard(material_type)
    if not standard:
        return []

    checks: list[MaterialCheck] = []
    if "材料类型匹配" in fields:
        value = fields.get("材料类型匹配")
        actual_type = str(fields.get("实际材料类型") or "未知")
        checks.append(
            MaterialCheck(
                rule="材料类型匹配",
                status="pass" if value is True else "fail" if value is False else "manual_review",
                reason="上传材料类型匹配" if value is True else f"实际识别为{actual_type}" if value is False else "无法确认材料类型是否匹配",
            )
        )

    file_format = str(fields.get("file_format") or fields.get("文件格式") or "").lower().lstrip(".")
    if file_format:
        checks.append(
            MaterialCheck(
                rule="文件格式符合要求",
                status="pass" if file_format in standard.accepted_formats else "fail",
                reason=f"文件格式为 {file_format}" if file_format in standard.accepted_formats else f"文件格式 {file_format} 不在要求范围：{', '.join(standard.accepted_formats)}",
            )
        )

    if material_type == "badge_photo":
        quality_checks = (("clear_image", "清晰可辨", "文件模糊、反光、遮挡或裁切"),)
    elif material_type == "bank_card":
        quality_checks = (
            ("clear_image", "清晰可辨", "文件模糊、反光、遮挡或裁切"),
            ("complete_document", "卡面信息完整", "银行卡卡面信息不完整"),
        )
    else:
        quality_checks = (
            ("color_scan", "彩色扫描/电子原件", "文件不是彩色扫描或电子原件"),
            ("clear_image", "清晰可辨", "文件模糊、反光、遮挡或裁切"),
            ("complete_document", "材料页完整", "材料页不完整"),
            ("original_scan", "非翻拍截图", "疑似翻拍、截图或压缩文件"),
        )
    for key, rule, fail_reason in quality_checks:
        if key not in fields:
            continue
        value = fields.get(key)
        checks.append(
            MaterialCheck(
                rule=rule,
                status="pass" if value is True else "fail" if value is False else "manual_review",
                reason="符合附件1上传标准" if value is True else fail_reason if value is False else "无法确认，需人工复核",
            )
        )

    orientation_checks = (
        ("all_pages_upright", "页面方向正向可读", "存在倒置、侧向或无法正向阅读的页面"),
        ("image_orientation_consistent", "文件内图像方向一致", "文件内多张图片或页面朝向不一致"),
    )
    for key, rule, fail_reason in orientation_checks:
        if key not in fields:
            continue
        value = fields.get(key)
        checks.append(
            MaterialCheck(
                rule=rule,
                status="pass" if _truthy(value) else "fail" if _falsey(value) else "manual_review",
                reason=_orientation_reason(fields, value, fail_reason),
            )
        )

    if material_type == "id_card" and "front_back_same_page" in fields:
        value = fields.get("front_back_same_page")
        checks.append(
            MaterialCheck(
                rule="证件正反面同页",
                status="pass" if value is True else "fail" if value is False else "manual_review",
                reason="正反面同页" if value is True else "正反面未放在同一页" if value is False else "无法确认正反面是否同页",
            )
        )
    return checks


def _required(field_name: str, value: Any) -> MaterialCheck:
    present = _has_value(value)
    return MaterialCheck(
        rule=f"必填字段：{field_name}",
        status="pass" if present else "fail",
        reason="已提取" if present else "缺少该字段",
    )


def _has_value(value: Any) -> bool:
    return value not in (None, "", False) and value != [] and value != {}


def _field_value(fields: dict[str, Any], field_name: str) -> Any:
    if field_name in fields:
        return fields.get(field_name)
    if "或" in field_name:
        return next((fields.get(item) for item in field_name.split("或") if fields.get(item)), None)
    if "/" in field_name:
        return next((fields.get(item) for item in field_name.split("/") if fields.get(item)), None)
    return None


def _first_field(fields: dict[str, Any], names: tuple[str, ...]) -> Any:
    return next((fields.get(name) for name in names if _has_value(fields.get(name))), None)


def _infer_award_name(ocr_text: str) -> str:
    text = " ".join((ocr_text or "").split())
    if not text:
        return ""
    fragments = re.split(r"[\n。；;，,：:\s]+", text)
    keywords = ("奖学金", "荣誉证书", "获奖证书", "奖励证书", "优秀学生", "优秀毕业生", "一等奖", "二等奖", "三等奖")
    for fragment in fragments:
        if any(keyword in fragment for keyword in keywords):
            return fragment[:80]
    match = re.search(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{2,50}(?:奖学金|荣誉|奖励|奖)", text)
    return match.group(0)[:80] if match else ""


def _infer_award_issuer(ocr_text: str) -> str:
    text = " ".join((ocr_text or "").split())
    if not text:
        return ""
    match = re.search(r"[\u4e00-\u9fffA-Za-z0-9·]{2,40}(?:大学|学院|学校|委员会|办公室|中心|协会|公司|单位)", text)
    return match.group(0) if match else ""


def _detected_types(fields: dict[str, Any]) -> list[str]:
    value = fields.get("检测到的材料类型列表")
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(fields.get("实际材料类型") or "")
    return [text] if text else []


def _truthy(value: Any) -> bool:
    return value in (True, "true", "True", "是", "有", "detected", "yes", "YES", "ICBC", "工商银行")


def _falsey(value: Any) -> bool:
    return value in (False, "false", "False", "否", "无", "not_detected", "no", "NO")


def _visual_bool_status(value: Any) -> str:
    if _truthy(value):
        return "pass"
    if _falsey(value):
        return "fail"
    return "manual_review"


def _visual_bool_reason(value: Any, pass_reason: str, fail_reason: str) -> str:
    if _truthy(value):
        return pass_reason
    if _falsey(value):
        return fail_reason
    return "无法确认，需人工复核"


def _orientation_reason(fields: dict[str, Any], value: Any, fail_reason: str) -> str:
    if _truthy(value):
        return "文件内页面朝向一致且正向可读"
    issue = fields.get("orientation_issue") or fields.get("页面朝向问题")
    if not issue:
        issue = fields.get("page_orientation_notes") or fields.get("页面方向说明")
    issue_text = _orientation_issue_text(issue)
    if _falsey(value):
        return f"{fail_reason}：{issue_text}" if issue_text else fail_reason
    return f"无法确认页面朝向，需人工复核：{issue_text}" if issue_text else "无法确认页面朝向，需人工复核"


def _orientation_issue_text(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value if item)[:200]
    if isinstance(value, dict):
        return "；".join(f"{key}:{item}" for key, item in value.items() if item)[:200]
    return str(value or "").strip()[:200]


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _name_check(candidate_name: str, actual_name: Any) -> MaterialCheck:
    matched, reason = _name_match_result(candidate_name, actual_name)
    return MaterialCheck(
        rule="姓名一致",
        status="pass" if matched else "fail",
        reason=reason if matched else f"材料姓名为 {actual_name or '空'}",
    )


def _name_match_result(candidate_name: str, actual_name: Any) -> tuple[bool, str]:
    candidate = str(candidate_name or "").strip()
    actual = str(actual_name or "").strip()
    if not candidate or not actual:
        return False, ""
    if _compact_name(candidate) == _compact_name(actual):
        return True, "姓名一致"
    if _candidate_name_appears_as_token(candidate, actual):
        return True, "材料姓名包含候选人中文名"
    actual_latin = _latin_name_key(actual)
    for alias in _candidate_pinyin_aliases(candidate):
        if alias and alias in actual_latin:
            return True, "姓名拼音与候选人中文名一致"
    return False, ""


def _candidate_name_appears_as_token(candidate_name: str, actual_name: str) -> bool:
    escaped = re.escape(candidate_name)
    return re.search(rf"(?<![\u4e00-\u9fff]){escaped}(?![\u4e00-\u9fff])", actual_name) is not None


def _compact_name(value: str) -> str:
    return re.sub(r"[\s·.()（）\[\]【】_-]+", "", value).casefold()


def _latin_name_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.casefold())


@lru_cache(maxsize=256)
def _candidate_pinyin_aliases(candidate_name: str) -> tuple[str, ...]:
    try:
        from pypinyin import lazy_pinyin  # type: ignore[import-not-found]
    except ImportError:
        return ()
    parts = [part for part in lazy_pinyin(candidate_name) if part]
    if not parts:
        return ()
    aliases = {"".join(parts)}
    if len(parts) > 1:
        aliases.add("".join(reversed(parts)))
    return tuple(alias for alias in aliases if len(alias) >= 4)


def _id_number_check(id_number: Any) -> MaterialCheck:
    value = str(id_number or "").upper()
    if not re.fullmatch(r"\d{17}[\dX]", value):
        return MaterialCheck(rule="身份证号格式和校验位", status="fail", reason="身份证号格式不正确")
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    codes = "10X98765432"
    total = sum(int(value[i]) * weights[i] for i in range(17))
    expected = codes[total % 11]
    return MaterialCheck(
        rule="身份证号格式和校验位",
        status="pass" if value[-1] == expected else "fail",
        reason="身份证号校验通过" if value[-1] == expected else "身份证号校验位不正确",
    )


def _date_not_expired(rule: str, value: Any) -> MaterialCheck:
    parsed = _parse_date(value)
    return MaterialCheck(
        rule=rule,
        status="pass" if parsed and parsed >= date.today() else "fail",
        reason="日期有效" if parsed and parsed >= date.today() else "日期已过期或无法识别",
    )


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).replace("年", "-").replace("月", "-").replace("日", "")
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _decision_from_checks(checks: list[MaterialCheck]) -> str:
    if any(check.status == "manual_review" for check in checks):
        return "manual_review"
    if any(check.status == "fail" for check in checks):
        return "auto_rejected"
    return "auto_approved"


def _candidate_message(decision: str) -> str:
    if decision == "auto_approved":
        return "材料已通过自动审核。"
    if decision == "auto_rejected":
        return "材料未通过自动审核，请根据提示补充或重新提交。"
    return "材料已收到，将转由 HR 人工审核。"


def _extract_fields_from_text(ocr_text: str) -> dict[str, Any]:
    # TODO: Replace with OCR/NLP extraction pipeline.
    fields: dict[str, Any] = {}
    name_match = re.search(r"姓名[:：]\s*([\u4e00-\u9fa5A-Za-z ]+)", ocr_text)
    if name_match:
        fields["姓名"] = name_match.group(1).strip()
    id_match = re.search(r"\b\d{17}[\dXx]\b", ocr_text)
    if id_match:
        fields["身份证号"] = id_match.group(0).upper()
    return fields
