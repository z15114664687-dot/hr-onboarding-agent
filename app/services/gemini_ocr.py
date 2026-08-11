from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.untrusted_content import UNTRUSTED_DOCUMENT_NOTICE
from app.services.material_standards import material_standard_prompt, material_visual_reference_prompt

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
OCR_REQUEST_ATTEMPTS = 2


class GeminiOcrError(RuntimeError):
    """Controlled OCR error safe to surface to calling services."""


async def extract_material_with_gemini(file_path: str, material_type: str) -> dict[str, Any]:
    """Use Gemini multimodal OCR to extract fields and upload-quality signals."""

    settings = get_settings()
    if not settings.allow_external_ai:
        raise RuntimeError("external AI processing is disabled")
    path = Path(file_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"file not found: {path}")

    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    content = path.read_bytes()
    if settings.gemini_gateway_url and settings.gemini_gateway_token:
        return await _extract_material_with_gateway(
            file_name=path.name,
            content=content,
            mime_type=mime_type,
            material_type=material_type,
        )
    if settings.gemini_api_key:
        return await extract_material_with_gemini_content(
            file_name=path.name,
            content=content,
            mime_type=mime_type,
            material_type=material_type,
        )
    raise RuntimeError("Gemini material OCR is not configured")


async def extract_material_with_gemini_content(
    *,
    file_name: str,
    content: bytes,
    mime_type: str,
    material_type: str,
) -> dict[str, Any]:
    """Use the local Gemini key to review uploaded material bytes."""

    settings = get_settings()
    if not settings.allow_external_ai:
        raise RuntimeError("external AI processing is disabled")
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    encoded = base64.b64encode(content).decode("ascii")
    prompt = _ocr_prompt(material_type, file_name)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": encoded}},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "topP": 0.5,
            "maxOutputTokens": 2400,
            "responseMimeType": "application/json",
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await _post_with_retries(
            client,
            url,
            headers={"x-goog-api-key": settings.gemini_api_key},
            json_payload=payload,
            service_name="Gemini OCR",
        )
    if response.status_code >= 400:
        raise GeminiOcrError(f"Gemini OCR request failed with status {response.status_code}")
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise GeminiOcrError("Gemini OCR API returned invalid JSON") from exc
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text", "")) for part in parts).strip()
    if not text:
        raise GeminiOcrError("Gemini OCR returned empty response")
    try:
        return _parse_json_response(text)
    except json.JSONDecodeError as exc:
        raise GeminiOcrError("Gemini OCR returned malformed extraction JSON") from exc


async def _extract_material_with_gateway(
    *,
    file_name: str,
    content: bytes,
    mime_type: str,
    material_type: str,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_gateway_url or not settings.gemini_gateway_token:
        raise RuntimeError("GEMINI_GATEWAY_URL or GEMINI_GATEWAY_TOKEN is not configured")

    timeout_seconds = settings.gemini_gateway_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    url = f"{settings.gemini_gateway_url.rstrip('/')}/internal/gemini/ocr"
    payload = {
        "file_name": file_name,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "mime_type": mime_type,
        "material_type": material_type,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await _post_with_retries(
            client,
            url,
            headers={"Authorization": f"Bearer {settings.gemini_gateway_token}"},
            json_payload=payload,
            service_name="Gemini OCR gateway",
        )
    if response.status_code >= 400:
        raise GeminiOcrError(_gateway_error_message(response))
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise GeminiOcrError("Gemini gateway OCR returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise GeminiOcrError("Gemini gateway OCR returned invalid response")
    return data


async def _post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json_payload: dict[str, Any],
    service_name: str,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(OCR_REQUEST_ATTEMPTS):
        try:
            response = await client.post(url, headers=headers, json=json_payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < OCR_REQUEST_ATTEMPTS - 1:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise GeminiOcrError(f"{service_name} request failed or timed out") from exc
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < OCR_REQUEST_ATTEMPTS - 1:
            await asyncio.sleep(0.2 * (attempt + 1))
            continue
        return response
    raise GeminiOcrError(f"{service_name} request failed") from last_error


def _gateway_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except json.JSONDecodeError:
        data = {}
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str) and detail:
        return detail
    return f"Gemini gateway OCR failed with status {response.status_code}"


def is_material_gemini_available() -> bool:
    settings = get_settings()
    return settings.allow_external_ai and bool(
        settings.gemini_api_key or (settings.gemini_gateway_url and settings.gemini_gateway_token)
    )


def _ocr_prompt(material_type: str, file_name: str) -> str:
    return f"""
你是 HR 入职材料多模态识别与初审助手。你不是只做 OCR，还要直接观察图片/PDF版式、照片内容、材料类别和页数完整性。

SECURITY BOUNDARY: {UNTRUSTED_DOCUMENT_NOTICE}

文件名：{file_name}
材料类型：{material_type}

请严格依据以下标准判断：
{material_standard_prompt(material_type)}

附件1上传文件示例的视觉参考：
{material_visual_reference_prompt(material_type)}

视觉判断要求：
- 判断上传文件实际像什么材料，不要只依赖文件名或文字。
- 如果材料类型不匹配，例如身份证位置上传了银行卡、学历证书位置上传了学信网报告，应明确标记。
- 对学历/学位证书，必须区分学校发放的毕业证书和学位证书，并判断是否两类都已提交。
- 对学历/学位证书，如为英文或中英文证书，学校、专业、学历层次、学位类别、证书名称等字段优先输出中文翻译，另在原文字段中保留原文，并写明翻译说明。
- 对求职简历，主动提取教育经历、工作经历、实习经历、职称/资格、获奖信息，能拆分就放入对应数组；无法可靠拆分时不要判失败，把可读正文保留在 ocr_text，并给出简历摘要。
- 对获奖证明，不要求版式完全等同附件示例；重点识别获奖人姓名、奖项名称、发证学校/机构或公章/落款。
- 对英文或中英文证明，字段名仍使用中文；奖项、机构等字段优先输出中文翻译，另在原文字段中保留英文原文，并写明翻译说明。
- 对姓名，中文名、英文名、拼音名可视为同一人；例如“林晓安”和“LIN Xiaoan”可判为一致。
- 逐页判断图片/PDF方向：每页必须正向可读；同一文件内多张图片或多页材料朝向必须一致。若存在倒置、侧向、横竖混排或前后页朝向不一致，设置对应方向字段为 false，并在问题说明里指出页码或图片序号。
- 对工作证照片，必须判断是否单人、五官清晰、服装和照片类型是否接近附件1示例。
- 对工资卡，必须判断是否为中国工商银行/ICBC；银行卡通常没有姓名，姓名缺失不要作为问题。

请只输出 JSON，不要输出解释。JSON schema:
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
    "公章文字": "",
    "简历摘要": "",
    "原文语言": "",
    "翻译说明": "",
    "在线验证码": "",
    "报告有效期": "",
    "认证书编号": "",
    "院校": "",
    "国别/地区": "",
    "认证结论": "",
    "原单位名称": "",
    "离职日期": "",
    "解除日期": "",
    "终止日期": "",
    "退工日期": "",
    "体检日期": "",
    "体检机构": "",
    "总检结论": "",
    "盖章识别结果": true,
    "有结论页": true,
    "户口本首页": true,
    "个人常住人口登记卡页": true,
    "登记事项变更页": true,
    "封面": true,
    "个人照片页": true,
    "盖章页": true,
    "账户状态或未开户证明": "",
    "银行名称": "",
    "银行卡号": "",
    "是否工商银行卡": true,
    "卡面是否显示姓名": false,
    "人像清晰": true,
    "单人照片": true,
    "五官清晰": true,
    "正式服装": true,
    "不合格照片类型": "",
    "照片大小": "",
    "file_format": "",
    "file_size_kb": 0,
    "color_scan": true,
    "clear_image": true,
    "complete_document": true,
    "original_scan": true,
    "front_back_same_page": true,
    "missing_back_side": false
  }},
  "quality_notes": ["发现的问题或无法确认点"]
}}

无法识别的字段请设为空字符串或 null；无法确认的布尔值请设为 null。
""".strip()


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return json.loads(cleaned)
