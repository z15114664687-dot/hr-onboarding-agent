from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.untrusted_content import UNTRUSTED_DOCUMENT_NOTICE, wrap_untrusted_document
from app.services.material_standards import material_standard_prompt, material_visual_reference_prompt

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
ARK_REQUEST_ATTEMPTS = 2


class ArkMaterialOcrError(RuntimeError):
    """Controlled Ark material OCR error."""


def is_ark_material_ocr_available() -> bool:
    settings = get_settings()
    return settings.allow_external_ai and bool(settings.ark_api_key and settings.ark_model)


async def extract_material_with_ark_content(
    *,
    file_name: str,
    content: bytes,
    mime_type: str,
    material_type: str,
) -> dict[str, Any]:
    """Use Volcengine Ark/Doubao vision as a direct OCR fallback."""

    settings = get_settings()
    if not settings.allow_external_ai:
        raise ArkMaterialOcrError("external AI processing is disabled")
    if not settings.ark_api_key:
        raise ArkMaterialOcrError("ARK_API_KEY is not configured")
    file_input = _content_file_input(file_name, content, mime_type)
    payload = {
        "model": settings.ark_model,
        "input": [
            {
                "role": "user",
                "content": [
                    file_input,
                    {"type": "input_text", "text": _ark_ocr_prompt(material_type, file_name)},
                ],
            }
        ],
        "temperature": 0,
        "max_output_tokens": 2400,
    }
    timeout = httpx.Timeout(settings.ark_timeout_seconds, connect=min(settings.ark_timeout_seconds, 10.0))
    url = f"{settings.ark_base_url.rstrip('/')}/responses"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await _post_with_retries(
            client,
            url,
            headers={"Authorization": f"Bearer {settings.ark_api_key}", "Content-Type": "application/json"},
            json_payload=payload,
        )
    if response.status_code >= 400:
        raise ArkMaterialOcrError(_ark_error_message(response))
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ArkMaterialOcrError("Ark OCR returned invalid JSON") from exc
    text = _extract_response_text(data)
    if not text:
        raise ArkMaterialOcrError("Ark OCR returned empty response")
    try:
        return _parse_json_response(text)
    except json.JSONDecodeError as exc:
        if material_type == "resume" and len(text.strip()) >= 10:
            return _resume_text_response(text)
        raise ArkMaterialOcrError("Ark OCR returned malformed extraction JSON") from exc


async def compare_material_requirements_with_ark(
    *,
    material_type: str,
    candidate_name: str,
    requirements: list[str],
    resume_context: dict[str, Any],
    material_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Use Ark/Doubao to compare extracted proof fields against resume requirements."""

    settings = get_settings()
    if not settings.allow_external_ai:
        raise ArkMaterialOcrError("external AI processing is disabled")
    if not settings.ark_api_key:
        raise ArkMaterialOcrError("ARK_API_KEY is not configured")
    payload = {
        "model": settings.ark_model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": _ark_requirement_compare_prompt(
                            material_type=material_type,
                            candidate_name=candidate_name,
                            requirements=requirements,
                            resume_context=resume_context,
                            material_evidence=material_evidence,
                        ),
                    }
                ],
            }
        ],
        "temperature": 0,
        "max_output_tokens": 1200,
    }
    timeout = httpx.Timeout(settings.ark_timeout_seconds, connect=min(settings.ark_timeout_seconds, 10.0))
    url = f"{settings.ark_base_url.rstrip('/')}/responses"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await _post_with_retries(
            client,
            url,
            headers={"Authorization": f"Bearer {settings.ark_api_key}", "Content-Type": "application/json"},
            json_payload=payload,
        )
    if response.status_code >= 400:
        raise ArkMaterialOcrError(_ark_error_message(response))
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ArkMaterialOcrError("Ark comparison returned invalid JSON") from exc
    text = _extract_response_text(data)
    if not text:
        raise ArkMaterialOcrError("Ark comparison returned empty response")
    try:
        parsed = _parse_json_response(text)
    except json.JSONDecodeError as exc:
        raise ArkMaterialOcrError("Ark comparison returned malformed JSON") from exc
    return parsed if isinstance(parsed, dict) else {}


def _content_file_input(file_name: str, content: bytes, mime_type: str) -> dict[str, str]:
    data_url = f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"
    if mime_type.startswith("image/"):
        return {"type": "input_image", "image_url": data_url}
    if mime_type == "application/pdf":
        return {"type": "input_file", "file_data": data_url, "filename": file_name}
    raise ArkMaterialOcrError("Ark material OCR fallback supports image and PDF uploads only")


async def _post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json_payload: dict[str, Any],
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(ARK_REQUEST_ATTEMPTS):
        try:
            response = await client.post(url, headers=headers, json=json_payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < ARK_REQUEST_ATTEMPTS - 1:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise ArkMaterialOcrError("Ark OCR request failed or timed out") from exc
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < ARK_REQUEST_ATTEMPTS - 1:
            await asyncio.sleep(0.2 * (attempt + 1))
            continue
        return response
    raise ArkMaterialOcrError("Ark OCR request failed") from last_error


def _extract_response_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text.strip()

    texts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts).strip()


def _ark_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return f"Ark OCR failed with status {response.status_code}"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
        if code == "ModelNotOpen":
            return "Ark model is not activated in Volcano Ark console"
        if message:
            return f"Ark OCR failed: {code or response.status_code} {message}"
    return f"Ark OCR failed with status {response.status_code}"


def _ark_ocr_prompt(material_type: str, file_name: str) -> str:
    return f"""
你是 HR 入职材料多模态识别与初审助手。请观察图片内容，提取文字和视觉质量信号。

SECURITY BOUNDARY: {UNTRUSTED_DOCUMENT_NOTICE}

文件名：{file_name}
材料类型：{material_type}

请严格依据以下标准判断：
{material_standard_prompt(material_type)}

附件1上传文件示例的视觉参考：
{material_visual_reference_prompt(material_type)}

必须判断：
- 实际材料类型是否匹配上传位置。
- 对学历/学位证书，判断是否为学校发放的毕业证书/学位证书，不能用学信网报告替代。
- 对学历/学位证书，如为英文或中英文证书，学校、专业、学历层次、学位类别、证书名称等字段优先输出中文翻译，另在原文字段中保留原文，并写明翻译说明。
- 对求职简历，主动提取教育经历、工作经历、实习经历、职称/资格、获奖信息，能拆分就放入对应数组；无法可靠拆分时不要判失败，把可读正文保留在 ocr_text，并给出简历摘要。
- 对职称/资格证书和获奖证明，不要求版式完全等同附件示例；重点识别持有人/获奖人姓名、证书/奖项名称、发证学校/机构或公章/落款，以及发放时间/颁发时间。
- 对英文或中英文证明，字段名仍使用中文；奖项、机构等字段优先输出中文翻译，另在原文字段中保留英文原文，并写明翻译说明。
- 对姓名，中文名、英文名、拼音名可视为同一人；例如“林晓安”和“LIN Xiaoan”可判为一致。
- 逐页判断图片/PDF方向：每页必须正向可读；同一文件内多张图片或多页材料朝向必须一致。若存在倒置、侧向、横竖混排或前后页朝向不一致，设置对应方向字段为 false，并在问题说明里指出页码或图片序号。
- 判断文件是否清晰、完整、彩色扫描或电子原件。

只输出 JSON，不要解释。JSON schema:
{{
  "ocr_text": "提取到的主要文字，控制在4000字以内",
  "extracted_fields": {{
    "实际材料类型": "",
    "检测到的材料类型列表": [],
    "材料类型匹配": true,
    "页数估计": 0,
    "all_pages_upright": true,
    "image_orientation_consistent": true,
    "orientation_issue": "",
    "page_orientation_notes": [],
    "姓名": "",
    "原文姓名": "",
    "身份证号": "",
    "有效期限": "",
    "学校": "",
    "原文学校": "",
    "专业": "",
    "原文专业": "",
    "学历层次": "",
    "原文学历层次": "",
    "学位类别": "",
    "原文学位类别": "",
    "证书编号": "",
    "证书类型": "",
    "证书名称": "",
    "原文证书名称": "",
    "职称": "",
    "资格": "",
    "检测到毕业证书": true,
    "检测到学位证书": true,
    "疑似学信网报告": false,
    "求学经历": [],
    "工作经历": [],
    "实习经历": [],
    "职称资格": [],
    "获奖信息": [],
    "奖项名称": "",
    "原文奖项名称": "",
    "发证机构": "",
    "原文发证机构": "",
    "颁发单位": "",
    "原文颁发单位": "",
    "发放时间": "",
    "公章文字": "",
    "简历摘要": "",
    "原文语言": "",
    "翻译说明": "",
    "银行名称": "",
    "银行卡号": "",
    "是否工商银行卡": true,
    "人像清晰": true,
    "单人照片": true,
    "五官清晰": true,
    "正式服装": true,
    "不合格照片类型": "",
    "color_scan": true,
    "clear_image": true,
    "complete_document": true,
    "original_scan": true,
    "盖章识别结果": true,
    "front_back_same_page": true,
    "missing_back_side": false
  }},
  "quality_notes": ["发现的问题或无法确认点"]
}}

无法识别的字段请设为空字符串或 null；无法确认的布尔值请设为 null。
""".strip()


def _ark_requirement_compare_prompt(
    *,
    material_type: str,
    candidate_name: str,
    requirements: list[str],
    resume_context: dict[str, Any],
    material_evidence: dict[str, Any],
) -> str:
    untrusted_evidence = wrap_untrusted_document(
        json.dumps(
            {
                "candidate_name": candidate_name,
                "requirements": requirements,
                "resume_context": resume_context,
                "material_evidence": material_evidence,
            },
            ensure_ascii=False,
        )
    )
    return json.dumps(
        {
            "role": "你是严格的HR入职材料一致性审核员，只判断证明材料是否能对应简历中列明的项目。",
            "material_type": material_type,
            "security_boundary": UNTRUSTED_DOCUMENT_NOTICE,
            "untrusted_document_evidence": untrusted_evidence,
            "rules": [
                "只能依据输入的简历上下文、OCR字段、OCR文本判断；不要补充常识或猜测。",
                "姓名不一致时必须判不匹配；但中文名、英文名、拼音名可视为同一人，例如“林晓安”和“LIN Xiaoan”可判匹配；姓名缺失时除非其他字段非常明确，否则判不匹配。",
                "职称/资格/获奖证明需证书或奖项名称语义一致，并且发证机构、学校、公章或落款能支持该项目。",
                "奖项名称可以存在简称、等级差异或学校前缀差异，例如“星河大学优秀学生”和“优秀学生”可在发证机构为星河大学时视为同一项目。",
                "学历/学位证书需与简历学校、学历层次或学位类别对应；毕业证书和学位证书分开判断；学信网报告不能替代学校证书。",
                "如果只是泛泛包含“优秀”“奖学金”“证书”等通用词，不能判匹配。",
                "只有证据充分时才给 high；不确定时给 low 并 matched=false。",
            ],
            "output_schema": {
                "matches": [
                    {
                        "label": "必须原样返回 requirements 中的某一项",
                        "matched": True,
                        "confidence": "high|medium|low",
                        "reason": "一句话说明证据",
                    }
                ]
            },
            "output_requirement": "只输出JSON，不要解释。对每个 requirement 都返回一条 matches。",
        },
        ensure_ascii=False,
    )


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return json.loads(cleaned)


def _resume_text_response(text: str) -> dict[str, Any]:
    compact = " ".join(text.split())
    return {
        "ocr_text": compact[:4000],
        "extracted_fields": {
            "实际材料类型": "求职简历",
            "检测到的材料类型列表": ["求职简历"],
            "材料类型匹配": True,
            "简历摘要": compact[:500],
        },
        "quality_notes": ["Ark 返回了非 JSON 文本，已按简历正文兜底处理。"],
    }
