from __future__ import annotations

import json
import re
from typing import Any

TEXT_HEAVY_KEYS = {
    "ocr_text",
    "OCR文本",
    "text",
    "content",
    "正文",
    "全文",
    "原文",
    "原文内容",
    "主要文字",
    "简历全文",
    "简历正文",
    "简历摘要",
}

SUMMARY_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "id_card": ("姓名", "身份证号", "有效期限", "签发机关"),
    "degree_certificate": (
        "姓名",
        "学校",
        "原文学校",
        "专业",
        "原文专业",
        "学历层次",
        "原文学历层次",
        "学位类别",
        "原文学位类别",
        "证书名称",
        "原文证书名称",
        "证书编号",
        "证书类型",
        "检测到毕业证书",
        "检测到学位证书",
        "翻译说明",
    ),
    "xuexin_report": ("姓名", "学校", "专业", "学历层次", "学位类别", "在线验证码", "报告有效期"),
    "overseas_degree_certification": ("姓名", "院校", "国别/地区", "认证书编号", "认证结论"),
    "employment_agreement": ("姓名", "学校", "专业", "就业协议或推荐表", "检测到的材料类型列表"),
    "professional_certificate": ("姓名", "证书名称", "职称", "资格", "发证机构", "颁发单位", "发放时间", "公章文字", "证书编号", "翻译说明"),
    "award_certificate": ("姓名", "奖项名称", "获奖名称", "荣誉名称", "发证机构", "颁发单位", "发放时间", "公章文字", "翻译说明"),
    "resignation_certificate": ("姓名", "原单位名称", "离职日期", "解除日期", "终止日期", "盖章识别结果"),
    "medical_report": ("姓名", "体检日期", "体检机构", "总检结论", "有结论页"),
    "hukou_material": ("姓名", "户口本首页", "个人常住人口登记卡页", "登记事项变更页", "盖章页"),
    "bank_card": ("姓名", "银行名称", "银行卡号", "是否工商银行卡", "账户状态或未开户证明"),
    "securities_statement": ("姓名", "账户状态或未开户证明", "证券公司", "股东账号", "资金账号"),
    "labor_termination_form": ("姓名", "原单位名称", "退工日期", "盖章识别结果"),
    "employment_manual": ("姓名", "单位名称", "就业登记日期", "盖章识别结果"),
}


def normalize_extracted_fields(
    material_type: str,
    fields: dict[str, Any],
    *,
    ocr_text: str = "",
    candidate_name: str = "",
) -> dict[str, Any]:
    normalized = dict(fields or {})
    normalized = _normalize_issue_date_field(material_type, normalized)
    if material_type != "resume":
        return normalized
    return _normalize_resume_fields(normalized, ocr_text=ocr_text, candidate_name=candidate_name)


def material_summary_text(
    material_type: str,
    fields: dict[str, Any],
    *,
    ocr_text: str = "",
    candidate_name: str = "",
) -> str:
    normalized = normalize_extracted_fields(material_type, fields, ocr_text=ocr_text, candidate_name=candidate_name)
    summary = material_summary_dict(material_type, normalized, ocr_text=ocr_text, candidate_name=candidate_name)
    if summary:
        return "\n".join(f"{key}：{_summary_value_text(value)}" for key, value in summary.items() if _has_value(value))[:1800]
    return _compact_text(ocr_text, 400)


def material_summary_json(
    material_type: str,
    fields: dict[str, Any],
    *,
    ocr_text: str = "",
    candidate_name: str = "",
) -> str:
    normalized = normalize_extracted_fields(material_type, fields, ocr_text=ocr_text, candidate_name=candidate_name)
    summary = material_summary_dict(material_type, normalized, ocr_text=ocr_text, candidate_name=candidate_name)
    return json.dumps(summary, ensure_ascii=False)


def material_summary_dict(
    material_type: str,
    fields: dict[str, Any],
    *,
    ocr_text: str = "",
    candidate_name: str = "",
) -> dict[str, Any]:
    if material_type == "resume":
        normalized = normalize_extracted_fields(material_type, fields, ocr_text=ocr_text, candidate_name=candidate_name)
        return _resume_summary_dict(normalized, candidate_name=candidate_name)

    selected: dict[str, Any] = {}
    for key in SUMMARY_FIELD_KEYS.get(material_type, ()):
        if key in fields and _has_value(fields.get(key)):
            selected[key] = _trim_summary_value(fields.get(key))
    translation_note = _translation_note(material_type, fields, ocr_text)
    if translation_note and "翻译说明" not in selected:
        selected["翻译说明"] = translation_note
    if selected:
        return selected
    return _generic_summary_dict(fields)


def _normalize_resume_fields(fields: dict[str, Any], *, ocr_text: str, candidate_name: str) -> dict[str, Any]:
    text = _resume_source_text(fields, ocr_text)
    if candidate_name and not _has_value(fields.get("姓名")) and candidate_name in text:
        fields["姓名"] = candidate_name
    if not _has_value(fields.get("求学经历")):
        educations = _extract_education_items(text)
        if educations:
            fields["求学经历"] = educations
    if not _has_value(fields.get("获奖信息")):
        awards = _extract_award_items(text)
        if awards:
            fields["获奖信息"] = awards
    if not _has_value(fields.get("职称资格")):
        certificates = _extract_certificate_items(text)
        if certificates:
            fields["职称资格"] = certificates
    if not _has_value(fields.get("实习经历")):
        internships = _extract_experience_items(text, ("实习经历", "实习经验"))
        if internships:
            fields["实习经历"] = internships
    if not _has_value(fields.get("工作经历")):
        jobs = _extract_experience_items(text, ("工作经历", "工作经验", "任职经历"))
        if jobs:
            fields["工作经历"] = jobs
    if text and not _has_value(fields.get("简历摘要")):
        fields["简历摘要"] = _compact_text(text, 500)
    return fields


def _normalize_issue_date_field(material_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    if material_type not in {"professional_certificate", "award_certificate"}:
        return fields
    if _has_value(fields.get("发放时间")):
        return fields
    for key in ("颁发时间", "颁发日期", "发证时间", "发证日期", "授予时间", "授予日期", "落款日期"):
        if _has_value(fields.get(key)):
            fields["发放时间"] = fields.get(key)
            break
    return fields


def _resume_summary_dict(fields: dict[str, Any], *, candidate_name: str = "") -> dict[str, Any]:
    summary = {
        "姓名": _first_text(fields.get("姓名")) or candidate_name,
        "学历学校": _extract_named_items(fields.get("求学经历"), ("学校", "院校", "毕业院校", "学历学校")),
        "奖项": _extract_named_items(fields.get("获奖信息"), ("奖项名称", "名称", "荣誉名称", "证书名称")),
        "职称资格": _extract_named_items(fields.get("职称资格"), ("名称", "证书名称", "职称", "资格")),
        "实习经历": _extract_experiences(fields.get("实习经历")),
        "工作经历": _extract_experiences(fields.get("工作经历")),
    }
    return {key: value for key, value in summary.items() if _has_value(value)}


def _resume_source_text(fields: dict[str, Any], ocr_text: str) -> str:
    parts = [ocr_text]
    for key in ("简历全文", "简历正文", "简历摘要", "ocr_text", "OCR文本", "正文", "全文"):
        value = fields.get(key)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(part for part in parts if part)


def _extract_education_items(text: str) -> list[dict[str, str]]:
    schools = _unique(
        match.group(0).strip(" ，,；;。")
        for match in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{2,30}(?:大学|学院|学校|University|College|Institute)", text)
    )
    return [{"学校": school} for school in schools[:8]]


def _extract_award_items(text: str) -> list[dict[str, str]]:
    lines = _section_lines(text, ("获奖", "荣誉", "奖励", "奖项")) or _text_lines(text)
    awards: list[str] = []
    for line in lines:
        if not any(keyword in line for keyword in ("奖", "荣誉", "优秀学生", "优秀毕业生", "竞赛")):
            continue
        matches = re.findall(
            r"[\u4e00-\u9fffA-Za-z0-9（）()·-]{2,50}(?:奖学金|一等奖|二等奖|三等奖|奖|荣誉称号)",
            line,
        )
        candidates = matches or [line]
        for candidate in candidates:
            cleaned = _clean_resume_item(candidate)
            if cleaned and cleaned not in awards and not _looks_like_section_header(cleaned):
                awards.append(cleaned)
    return [{"奖项名称": award} for award in awards[:12]]


def _extract_certificate_items(text: str) -> list[str]:
    lines = _section_lines(text, ("职称", "资格", "证书", "技能证书")) or _text_lines(text)
    certificates: list[str] = []
    for line in lines:
        if not any(keyword in line for keyword in ("资格", "证书", "CPA", "CFA", "从业", "职称")):
            continue
        cleaned = _clean_resume_item(line)
        if cleaned and cleaned not in certificates and not _looks_like_section_header(cleaned):
            certificates.append(cleaned)
    return certificates[:10]


def _extract_experience_items(text: str, headers: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in _section_lines(text, headers):
        cleaned = _clean_resume_item(line)
        if len(cleaned) < 4:
            continue
        if not any(keyword in cleaned for keyword in ("公司", "集团", "银行", "基金", "证券", "科技", "实习", "岗位", "经理", "分析", "运营", "产品")):
            continue
        item = {
            "公司": _extract_org(cleaned),
            "岗位": _extract_role(cleaned),
            "时间": _extract_duration(cleaned),
        }
        if not any(item.values()):
            item["内容"] = _compact_text(cleaned, 100)
        rows.append({key: value for key, value in item.items() if value})
        if len(rows) >= 12:
            break
    return rows


def _section_lines(text: str, headers: tuple[str, ...]) -> list[str]:
    marked = re.sub(r"(教育经历|教育背景|求学经历|工作经历|工作经验|任职经历|实习经历|实习经验|项目经历|获奖信息|获奖经历|荣誉奖励|奖励情况|职称资格|资格证书|技能证书)", r"\n\1\n", text)
    lines = _text_lines(marked)
    collected: list[str] = []
    in_section = False
    for line in lines:
        is_header = _looks_like_section_header(line)
        if any(header in line for header in headers):
            in_section = True
            continue
        if in_section and is_header:
            break
        if in_section:
            collected.append(line)
    return collected


def _text_lines(text: str) -> list[str]:
    normalized = re.sub(r"[；;]", "\n", text or "")
    normalized = re.sub(r"(?=(?:19|20)\d{2}[./年-])", "\n", normalized)
    return [_compact_text(line, 160) for line in normalized.splitlines() if _compact_text(line, 160)]


def _extract_org(text: str) -> str:
    match = re.search(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}(?:公司|集团|银行|基金|证券|科技|咨询|大学|学院)", text)
    return _compact_text(match.group(0), 60) if match else ""


def _extract_role(text: str) -> str:
    match = re.search(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{2,30}(?:实习生|分析师|经理|助理|运营|产品|研究员|工程师|顾问)", text)
    return _compact_text(match.group(0), 50) if match else ""


def _extract_duration(text: str) -> str:
    match = re.search(r"(?:19|20)\d{2}[./年-]\d{0,2}[^，,；;\n]{0,20}(?:至今|(?:19|20)\d{2}[./年-]?\d{0,2})?", text)
    return _compact_text(match.group(0), 40) if match else ""


def _clean_resume_item(text: str) -> str:
    value = re.sub(r"^\s*(获奖信息|获奖经历|荣誉奖励|奖励情况|职称资格|资格证书|技能证书)[:：]?", "", text)
    value = re.sub(r"^(?:19|20)\d{2}[./年-]?\d{0,2}\s*", "", value)
    return _compact_text(value.strip(" ，,；;。"), 100)


def _looks_like_section_header(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return compact in {
        "教育经历",
        "教育背景",
        "求学经历",
        "工作经历",
        "工作经验",
        "任职经历",
        "实习经历",
        "实习经验",
        "项目经历",
        "获奖信息",
        "获奖经历",
        "荣誉奖励",
        "奖励情况",
        "职称资格",
        "资格证书",
        "技能证书",
    }


def _generic_summary_dict(fields: dict[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for key, value in fields.items():
        if key in TEXT_HEAVY_KEYS or key.startswith("file_"):
            continue
        if not _has_value(value):
            continue
        if isinstance(value, str) and len(value) > 180:
            continue
        selected[key] = _trim_summary_value(value)
        if len(selected) >= 12:
            break
    return selected


def _translation_note(material_type: str, fields: dict[str, Any], ocr_text: str) -> str:
    if material_type not in {"degree_certificate", "award_certificate", "professional_certificate", "overseas_degree_certification"}:
        return ""
    explicit = fields.get("翻译说明")
    if _has_value(explicit):
        return _compact_text(explicit, 180)
    language = str(
        fields.get("原文语言")
        or fields.get("材料语言")
        or fields.get("document_language")
        or fields.get("language")
        or ""
    ).casefold()
    evidence = _translation_evidence(fields, ocr_text)
    if "英" in language or "english" in language or language == "en" or _looks_like_english_or_bilingual(evidence):
        return "英文/中英材料：字段名已按中文口径整理；字段值如为中文，为系统翻译结果。"
    return ""


def _translation_evidence(fields: dict[str, Any], ocr_text: str) -> str:
    parts = [ocr_text]
    for key, value in fields.items():
        if key in TEXT_HEAVY_KEYS or key.startswith("file_"):
            continue
        parts.append(_summary_value_text(value))
    return " ".join(part for part in parts if part)


def _looks_like_english_or_bilingual(text: str) -> bool:
    english_words = re.findall(r"\b[A-Za-z]{3,}\b", text or "")
    return len(english_words) >= 2


def _trim_summary_value(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_text(value, 180)
    if isinstance(value, list):
        trimmed = [_trim_summary_value(item) for item in value[:12]]
        return [item for item in trimmed if _has_value(item)]
    if isinstance(value, dict):
        return {str(key): _trim_summary_value(item) for key, item in value.items() if _has_value(item) and key not in TEXT_HEAVY_KEYS}
    return value


def _extract_named_items(value: Any, keys: tuple[str, ...]) -> list[str]:
    items: list[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            text = _first_text(next((item.get(key) for key in keys if item.get(key)), None))
            if not text:
                text = _compact_text(" ".join(str(part) for part in item.values() if part), 80)
        else:
            text = _compact_text(str(item), 80)
        if text and text not in items:
            items.append(text)
    return items[:12]


def _extract_experiences(value: Any) -> list[str]:
    rows: list[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            company = _first_text(item.get("公司") or item.get("单位") or item.get("机构") or item.get("组织"))
            role = _first_text(item.get("岗位") or item.get("职位") or item.get("职务"))
            duration = _first_text(item.get("时间") or item.get("起止时间") or item.get("时长") or item.get("期间"))
            text = " / ".join(part for part in (company, role, duration) if part)
            if not text:
                text = _compact_text(" ".join(str(part) for part in item.values() if part), 100)
        else:
            text = _compact_text(str(item), 100)
        if text and text not in rows:
            rows.append(text)
    return rows[:12]


def _summary_value_text(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(_summary_value_text(item) for item in value if _has_value(item))
    if isinstance(value, dict):
        return " / ".join(_summary_value_text(item) for item in value.values() if _has_value(item))
    return _compact_text(value, 180)


def _as_list(value: Any) -> list[Any]:
    if value in (None, "", False, [], {}):
        return []
    return value if isinstance(value, list) else [value]


def _first_text(value: Any) -> str:
    if value in (None, "", False, [], {}):
        return ""
    if isinstance(value, list):
        return _compact_text(" ".join(_first_text(item) for item in value), 100)
    if isinstance(value, dict):
        return _compact_text(" ".join(_first_text(item) for item in value.values()), 100)
    return _compact_text(str(value), 100)


def _has_value(value: Any) -> bool:
    if value in (None, "", False, [], {}):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _unique(values: Any) -> list[str]:
    items: list[str] = []
    for value in values:
        if value and value not in items:
            items.append(value)
    return items


def _compact_text(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]
