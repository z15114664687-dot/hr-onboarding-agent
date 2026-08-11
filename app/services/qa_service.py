from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.untrusted_content import UNTRUSTED_DOCUMENT_NOTICE, wrap_untrusted_document
from app.providers.base import LLMRequest
from app.providers.registry import get_llm_provider
from app.services.knowledge_base import KnowledgeChunk, retrieve_relevant_chunks

logger = logging.getLogger(__name__)

HANDOFF_TEXT = "这个问题我先不凭当前材料下结论，建议转 HRBP 或 SSC 确认。"
SENSITIVE_KEYWORDS = ("我的薪资", "个人薪资", "薪酬包", "offer薪资", "工资多少", "涨薪", "奖金", "绩效工资")
HR_SCOPE_KEYWORDS = (
    "入职",
    "预入职",
    "报到",
    "转正",
    "试用期",
    "材料",
    "体检",
    "背调",
    "社保",
    "公积金",
    "薪酬",
    "薪资",
    "工资",
    "福利",
    "假期",
    "劳动合同",
    "offer",
    "档案",
    "户口",
    "党团",
    "证券账户",
    "岗位资格",
    "合规承诺",
    "投资申报",
    "工作证",
    "工资卡",
    "人事",
    "hr",
    "ssc",
    "hrbp",
)
TECH_OUT_OF_SCOPE_KEYWORDS = (
    "infra",
    "gpu",
    "cuda",
    "大模型",
    "算力",
    "芯片",
    "transformer",
    "pytorch",
    "rocm",
    "triton",
    "asic",
    "lpu",
    "tpu",
)
INTERNAL_BENEFIT_KEYWORDS = (
    "福利待遇",
    "内部福利",
    "正式员工福利",
    "差旅",
    "出差",
    "住宿标准",
    "餐费补助",
    "交通补贴",
    "交通标准",
    "通讯补贴",
    "餐补",
    "补贴",
    "报销标准",
    "补充商业保险",
    "商业保险",
    "补充医疗",
    "体检套餐",
    "培训预算",
    "认证培训",
    "专家访谈",
    "数据库",
    "研究支持",
    "项目补贴",
    "客户拜访",
    "路演",
    "搬迁",
    "租房补贴",
    "外勤",
)
FILE_FIRST_KEYWORDS = (
    "入职",
    "入职流程",
    "入职材料",
    "材料",
    "预入职",
    "报到",
    "背调",
    "体检",
    "转正",
    "试用期",
    "转正标准",
    "转正条件",
    "转正考核",
    "转正流程",
    "转正手续",
    "试用期考察",
    "劳动合同",
    "offer",
    "档案",
    "户口",
    "党团",
    "证券账户",
    "岗位资格",
    "合规承诺",
    "投资申报",
    "工作证",
    "工资卡",
)
EXTERNAL_ADVICE_INTENT_KEYWORDS = (
    "一般",
    "通常",
    "行业",
    "公开",
    "经验",
    "攻略",
    "建议",
    "准备多久",
    "准备多长",
    "多久比较好",
    "多久合适",
    "备考多久",
    "复习多久",
    "怎么准备",
    "如何准备",
    "怎么处理比较好",
    "处理建议",
    "学习计划",
    "学习建议",
    "备考建议",
    "复习建议",
    "难不难",
    "难度",
    "注意事项",
    "需要注意",
    "要注意",
    "注意什么",
    "有什么坑",
    "避坑",
    "背景",
    "科普",
)
INTERNAL_REQUIREMENT_INTENT_KEYWORDS = (
    "公司",
    "本公司",
    "内部",
    "要求",
    "规定",
    "制度",
    "标准",
    "流程",
    "节点",
    "截止",
    "截止日期",
    "多久内",
    "提交",
    "上传",
    "审核",
    "补充",
    "通过",
    "办理",
    "签署",
    "确认",
    "入口",
    "怎么操作",
    "清单",
    "需要哪些",
    "有哪些材料",
    "入职材料",
    "转正标准",
    "转正条件",
)
PUBLIC_STATUTORY_POLICY_KEYWORDS = (
    "社保",
    "公积金",
    "最低工资",
    "工资标准",
    "缴费基数",
    "缴存基数",
    "医保",
    "养老",
    "失业保险",
    "工伤保险",
    "生育保险",
)
PUBLIC_POLICY_KEYWORDS = (
    "社保",
    "公积金",
    "最低工资",
    "工资标准",
    "薪酬标准",
    "薪资标准",
    "缴费基数",
    "缴存基数",
    "医保",
    "养老",
    "政策",
    "福利",
)
COMMON_POLICY_CITIES = (
    "香港",
    "澳门",
    "台湾",
    "英国",
    "美国",
    "新加坡",
    "日本",
    "韩国",
    "德国",
    "法国",
    "加拿大",
    "澳大利亚",
    "上海",
    "北京",
    "广州",
    "深圳",
    "杭州",
    "武汉",
    "南京",
    "苏州",
    "成都",
    "重庆",
    "西安",
    "天津",
    "厦门",
    "青岛",
    "宁波",
)


@dataclass(frozen=True)
class QAResult:
    """Answer and source metadata returned by the HR QA pipeline."""

    answer: str
    sources: list[dict[str, str]]
    needs_handoff: bool = False
    used_external_search: bool = False


async def answer_hr_question(
    question: str,
    *,
    user_tags: dict[str, str | list[str] | tuple[str, ...]] | None = None,
) -> QAResult:
    """Answer HR policy questions with local RAG and optional Gemini generation."""

    normalized_question = question.strip()
    if _is_sensitive_personal_question(normalized_question):
        return QAResult(
            answer="个人薪资、奖金、绩效、offer 薪酬包等属于敏感个人信息，我这里不能查询或推测。建议转 HRBP 或 SSC 确认。",
            sources=[],
            needs_handoff=True,
        )
    if _is_clear_out_of_scope_question(normalized_question):
        return QAResult(
            answer=(
                "这个问题不属于入职流程、HR政策或材料办理范围，我先不随意回答。"
                "如果你想问入职材料、转正、社保公积金、薪酬政策、证券账户或岗位资格等 HR 事项，可以直接告诉我具体问题。"
            ),
            sources=[],
        )

    settings = get_settings()
    chunks = retrieve_relevant_chunks(normalized_question, settings.knowledge_base_dir, user_tags=user_tags)
    internal_block_answer = _internal_benefit_access_block_answer(normalized_question, user_tags, chunks)
    if internal_block_answer:
        return QAResult(
            answer=internal_block_answer,
            sources=[],
            needs_handoff=True,
            used_external_search=False,
        )
    providers = _configured_llm_providers(settings)
    if not chunks and not providers:
        return QAResult(answer=HANDOFF_TEXT, sources=[], needs_handoff=True)

    llm_error: Exception | None = None
    requires_external_search = _should_use_external_search(normalized_question, chunks)
    has_external_search_provider = any(_provider_supports_external_search(provider) for provider in providers)
    for provider in providers:
        can_answer_from_local_chunks = _can_answer_from_local_chunks_without_search(normalized_question, chunks)
        if (
            requires_external_search
            and has_external_search_provider
            and not _provider_supports_external_search(provider)
            and not can_answer_from_local_chunks
        ):
            continue
        use_external_search = _provider_supports_external_search(provider) and requires_external_search
        if not chunks and not use_external_search:
            continue
        generation_chunks = chunks
        if use_external_search and not _should_include_local_chunks_with_external_search(normalized_question, chunks):
            generation_chunks = []
        try:
            answer, external_sources = await _generate_with_llm(
                normalized_question,
                generation_chunks,
                provider=provider,
                enable_external_search=use_external_search,
            )
            effective_external_search = use_external_search or _answer_claims_external_search(answer)
            sources = _answer_sources(
                generation_chunks,
                answer,
                external_sources,
                used_external_search=effective_external_search,
            )
            return QAResult(
                answer=answer,
                sources=sources,
                needs_handoff=_looks_like_handoff(answer),
                used_external_search=effective_external_search,
            )
        except Exception as exc:
            llm_error = exc
            logger.warning(
                "qa_llm_failed provider=%s model=%s error_type=%s",
                provider,
                _provider_model(settings, provider),
                type(exc).__name__,
            )

    if llm_error is not None:
        if requires_external_search and has_external_search_provider:
            return QAResult(
                answer=_external_search_unavailable_answer(normalized_question, llm_error=_llm_error_label(llm_error)),
                sources=[],
                needs_handoff=True,
                used_external_search=True,
            )
        return QAResult(
            answer=_fallback_answer(normalized_question, chunks, llm_error=_llm_error_label(llm_error)),
            sources=_source_payload(chunks),
            needs_handoff=not chunks,
        )

    return QAResult(answer=_fallback_answer(normalized_question, chunks), sources=_source_payload(chunks))


def format_qa_reply(result: QAResult) -> str:
    """Format a QA answer for Feishu text reply."""

    sources = result.sources or _external_search_fallback_sources() if result.used_external_search else result.sources
    if not sources:
        return result.answer
    source_lines = [
        f"Source: [{source.get('id', '-')}] {source.get('title', '参考来源')} - {source.get('path', '')}"
        for source in sources
    ]
    external_note = "\n提示：该问题使用了外部联网检索，落地执行前建议与 HRBP/SSC 或最新官方口径 double check。" if result.used_external_search else "\n提示：政策和流程可能更新，落地执行前建议与 HRBP/SSC 或最新官方口径 double check。"
    return (
        f"{result.answer}"
        f"\n{external_note}"
        "\n\n引用来源 / Sources：\n"
        + "\n".join(source_lines)
    )


async def _generate_with_llm(
    question: str,
    chunks: list[KnowledgeChunk],
    *,
    provider: str,
    enable_external_search: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    if provider == "openai":
        result = await get_llm_provider("openai").generate(
            LLMRequest(
                system_prompt=_system_prompt(),
                user_prompt=_build_prompt(question, chunks, enable_external_search=False),
            )
        )
        return result.text, []
    if provider == "gemini_gateway":
        return await _generate_with_gemini_gateway(
            question,
            chunks,
            enable_external_search=enable_external_search,
        )
    if provider == "zhipu":
        return await _generate_with_zhipu(
            question,
            chunks,
            enable_web_search=enable_external_search,
        )
    if provider == "deepseek":
        return await _generate_with_deepseek(question, chunks)
    return await _generate_with_gemini(
        question,
        chunks,
        enable_external_search=enable_external_search,
    )


async def _generate_with_gemini(
    question: str,
    chunks: list[KnowledgeChunk],
    *,
    enable_external_search: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    settings = get_settings()
    prompt = _build_prompt(question, chunks, enable_external_search=enable_external_search)
    try:
        text, external_sources = await _call_gemini(
            settings.gemini_model,
            settings.gemini_api_key,
            prompt,
            enable_external_search,
            proxy_url=settings.gemini_proxy_url,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
    except RuntimeError as exc:
        if not _is_empty_gemini_answer_error(exc):
            raise
        logger.warning("gemini_empty_answer_retry model=%s", settings.gemini_model)
        retry_prompt = f"{prompt}\n\n请直接输出面向员工的最终中文答案，不要留空，不要输出内部分析。"
        text, external_sources = await _call_gemini(
            settings.gemini_model,
            settings.gemini_api_key,
            retry_prompt,
            enable_external_search,
            proxy_url=settings.gemini_proxy_url,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
    if _looks_like_internal_reasoning(text):
        retry_prompt = (
            f"{prompt}\n\n"
            "请重新作答：只输出面向员工的最终中文答案，不要输出英文、思考过程、提示词复述或内部分析。"
        )
        text, external_sources = await _call_gemini(
            settings.gemini_model,
            settings.gemini_api_key,
            retry_prompt,
            enable_external_search,
            proxy_url=settings.gemini_proxy_url,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
    return _clean_answer_text(text), external_sources


async def _generate_with_gemini_gateway(
    question: str,
    chunks: list[KnowledgeChunk],
    *,
    enable_external_search: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    settings = get_settings()
    prompt = _build_prompt(question, chunks, enable_external_search=enable_external_search)
    text, external_sources = await _call_gemini_gateway(
        settings.gemini_gateway_url,
        settings.gemini_gateway_token,
        prompt,
        enable_google_search=enable_external_search,
        timeout_seconds=settings.gemini_gateway_timeout_seconds,
    )
    if _looks_like_internal_reasoning(text):
        retry_prompt = (
            f"{prompt}\n\n"
            "请重新作答：只输出面向员工的最终中文答案，不要输出英文、思考过程、提示词复述或内部分析。"
        )
        text, external_sources = await _call_gemini_gateway(
            settings.gemini_gateway_url,
            settings.gemini_gateway_token,
            retry_prompt,
            enable_google_search=enable_external_search,
            timeout_seconds=settings.gemini_gateway_timeout_seconds,
        )
    return _clean_answer_text(text), external_sources


async def _generate_with_deepseek(
    question: str,
    chunks: list[KnowledgeChunk],
) -> tuple[str, list[dict[str, str]]]:
    settings = get_settings()
    prompt = _build_prompt(question, chunks, enable_external_search=False)
    text = await _call_deepseek(
        settings.deepseek_model,
        settings.deepseek_api_key,
        prompt,
        base_url=settings.deepseek_base_url,
        timeout_seconds=settings.deepseek_timeout_seconds,
    )
    if _looks_like_internal_reasoning(text):
        retry_prompt = (
            f"{prompt}\n\n"
            "请重新作答：只输出面向员工的最终中文答案，不要输出英文、思考过程、提示词复述或内部分析。"
        )
        text = await _call_deepseek(
            settings.deepseek_model,
            settings.deepseek_api_key,
            retry_prompt,
            base_url=settings.deepseek_base_url,
            timeout_seconds=settings.deepseek_timeout_seconds,
        )
    return _clean_answer_text(text), []


async def _generate_with_zhipu(
    question: str,
    chunks: list[KnowledgeChunk],
    *,
    enable_web_search: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    settings = get_settings()
    prompt = _build_prompt(question, chunks, enable_external_search=enable_web_search)
    text, external_sources = await _call_zhipu_chat(
        settings.zhipu_text_model,
        settings.zhipu_api_key,
        prompt,
        base_url=settings.zhipu_base_url,
        timeout_seconds=settings.zhipu_timeout_seconds,
        enable_web_search=enable_web_search,
    )
    if _looks_like_internal_reasoning(text):
        retry_prompt = (
            f"{prompt}\n\n"
            "请重新作答：只输出面向员工的最终中文答案，不要输出英文、思考过程、提示词复述或内部分析。"
        )
        text, external_sources = await _call_zhipu_chat(
            settings.zhipu_text_model,
            settings.zhipu_api_key,
            retry_prompt,
            base_url=settings.zhipu_base_url,
            timeout_seconds=settings.zhipu_timeout_seconds,
            enable_web_search=enable_web_search,
        )
    return _clean_answer_text(text), external_sources


async def _call_gemini_gateway(
    gateway_url: str,
    token: str,
    prompt: str,
    *,
    enable_google_search: bool = False,
    timeout_seconds: float = 15.0,
) -> tuple[str, list[dict[str, str]]]:
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
    url = f"{gateway_url.rstrip('/')}/internal/gemini/generate"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": prompt, "enable_google_search": enable_google_search},
        )
        response.raise_for_status()
    data = response.json()
    text = str(data.get("answer") or "").strip()
    if not text:
        raise RuntimeError("Gemini gateway returned empty answer")
    sources = data.get("sources") if isinstance(data.get("sources"), list) else []
    return text, [source for source in sources if isinstance(source, dict)]


async def _call_gemini(
    model: str,
    api_key: str,
    prompt: str,
    enable_google_search: bool,
    *,
    proxy_url: str = "",
    timeout_seconds: float = 30.0,
) -> tuple[str, list[dict[str, str]]]:
    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": _system_prompt()}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "topP": 0.7, "maxOutputTokens": 3200},
    }
    if enable_google_search:
        payload["tools"] = [{"google_search": {}}]

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url or None) as client:
        response = await client.post(url, headers={"x-goog-api-key": api_key}, json=payload)
        response.raise_for_status()
    data = response.json()
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text", "")) for part in parts).strip()
    if not text:
        finish_reason = ((data.get("candidates") or [{}])[0] or {}).get("finishReason")
        raise RuntimeError(f"Gemini returned empty answer finish_reason={finish_reason}")
    return text, _extract_grounding_sources(data)


def _is_empty_gemini_answer_error(exc: Exception) -> bool:
    message = str(exc)
    return "Gemini returned empty answer" in message and "finish_reason=STOP" in message


async def _call_deepseek(
    model: str,
    api_key: str,
    prompt: str,
    *,
    base_url: str = "https://api.deepseek.com",
    timeout_seconds: float = 30.0,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 3200,
    }
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    url = f"{base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    text = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
    if not text:
        finish_reason = ((data.get("choices") or [{}])[0] or {}).get("finish_reason")
        raise RuntimeError(f"DeepSeek returned empty answer finish_reason={finish_reason}")
    return text


async def _call_zhipu_chat(
    model: str,
    api_key: str,
    prompt: str,
    *,
    base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    timeout_seconds: float = 60.0,
    enable_web_search: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2400,
    }
    if enable_web_search:
        payload["tools"] = [
            {
                "type": "web_search",
                "web_search": {
                    "enable": True,
                    "search_engine": "search_pro",
                    "search_result": True,
                    "count": 5,
                    "search_recency_filter": "noLimit",
                    "content_size": "high",
                },
            }
        ]
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    url = f"{base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    text = _extract_zhipu_response_text(data)
    if not text:
        raise RuntimeError("Zhipu returned empty answer")
    return text, _extract_zhipu_web_search_sources(data) if enable_web_search else []


def _looks_like_internal_reasoning(text: str) -> bool:
    markers = ("prompt says", "the prompt", "I will", "Wait,", "思考", "内部分析")
    return any(marker.lower() in text.lower() for marker in markers)


def _clean_answer_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"(?is)^\\*?wait,.*?(?=\\n\\s*[\\u4e00-\\u9fff])", "", cleaned).strip()
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^\s*\*\s+", "- ", cleaned)
    cleaned = re.sub(r"(?m)^(\s*)\*\s+", r"\1- ", cleaned)
    cleaned = cleaned.replace("*", "").replace("_", "")
    return cleaned


def _system_prompt() -> str:
    return (
        "你是公司 HR 入职协同助手，服务对象是候选人、新员工、HRBP、SSC 和用人经理。"
        f"{UNTRUSTED_DOCUMENT_NOTICE}"
        "你的语气像一位负责跟进入职事项的 HR 同事：温和、明确、不过度热情，不冒充真人，也不暴露系统实现。"
        "你的目标是基于检索到的受控知识库片段与官方政策文件，回答常见 HR 问题，提供政策查询和流程指引。"
        "必须遵守以下限制："
        "1. 优先使用用户问题下方给出的资料片段，不要编造。"
        "2. 使用本地资料回答时需要引用资料编号，例如 [S1]；使用外部搜索且没有本地资料编号时，不要编造 [S1] 编号，可直接写来源机构名称。"
        "3. 涉及个人薪资、奖金、绩效、offer 薪酬包、个人社保缴费明细等敏感信息时，不查询、不推测，转 HRBP 或 SSC。"
        "4. 政策类问题不要过度拒答。只要资料里有相关城市、地区、外部搜索结果或公司流程口径，就先给出可用结论；如果用户没有说明城市，优先使用资料中最相关的公司 base 地或公司流程口径，并提醒用户可补充城市进一步精确。"
        "5. 涉及社保、公积金、最低工资、转正、岗位资格、证券账户、党团组织关系、体检等问题时，不要只回复“因城市和员工类型不同请联系 HR”。应给出当前资料支持的规则、适用城市/期间、关键条件和下一步操作。"
        "6. 用户询问城市薪酬/薪资/工资标准，且没有问个人 offer、个人工资或奖金时，按公开政策口径回答最低工资、社保缴费基数、公积金缴存基数等，不要误判为个人薪资查询。"
        "7. 如果资料不足、政策口径冲突、城市不明确或问题需要人工判断，在给出已有结论后，用一句话提示需与 HRBP/SSC 或最新官方口径 double check；不要把 double check 当作唯一答案。"
        "8. 用户问其他城市政策时，如果本地资料未覆盖但启用了外部搜索，请优先引用政府、人社局、公积金中心、税务局等权威来源回答，并说明这是外部检索结果。"
        "9. 不提供法律承诺，不替代正式制度、劳动合同、offer 或 HR 书面确认。"
        "10. 公司内部福利、差旅、补贴、补充商业保险、体检套餐、培训预算等只能使用通过身份标签过滤后的本地资料回答；"
        "本地资料未命中时，不要用外部搜索或行业惯例补充。"
        "11. 转正、试用期、转正考核、转正流程等内部流程问题只能基于经过权限过滤的本地知识库回答，不要使用外部搜索或行业通用标准补充。"
        "12. 回答要简洁、可执行，中文输出；正文控制在 800 字以内，避免被消息平台截断。"
        "13. 非政策、非流程、非清单类问题优先用 1 到 3 句自然回答，不要强行列点。"
        "14. 政策、流程、材料清单、对比和多步骤办理问题可以使用列表或表格，但最多使用 6 条要点。"
        "15. 不要在正文末尾重复完整来源列表，系统会在答案后追加 Sources 区块；正文只保留 [S1] 这类引用标记。"
    )


def _build_prompt(question: str, chunks: list[KnowledgeChunk], *, enable_external_search: bool = False) -> str:
    comparison_note = _comparison_table_instruction(question)
    if enable_external_search and not chunks:
        if _needs_external_general_advice(question):
            return (
                f"用户问题：{question}\n\n"
                "本地知识库未找到直接覆盖该问题的资料。请使用外部联网检索补充公开资料、经验建议或官方说明，"
                "回答准备时长、注意事项、学习计划、难度、办理经验等实操建议。"
                "如果涉及公司要求，必须说明当前没有本地公司资料支持，不要把外部建议写成公司硬性要求。"
                "本题没有本地资料编号，不要编造 [S1]、[S2] 这类引用编号；如需说明依据，请写来源名称。"
                f"{comparison_note}"
            )
        return (
            f"用户问题：{question}\n\n"
            "本地知识库未找到直接覆盖该城市/问题的资料。请使用外部联网检索查询权威来源，"
            "优先选择政府、人社局、公积金中心、税务局等官方来源。请给出可用结论，并提示需 double check。"
            "本题没有本地资料编号，不要编造 [S1]、[S2] 这类引用编号；如需说明依据，请写官方机构名称。"
            "只有检索来源明确来自政府、人社局、公积金中心、税务局等官方机构时，才可称为“官方”或“权威”；"
            "如果来源是媒体、门户、百科、论坛或聚合页，只能称为“公开检索结果”，并提示需核验官方口径。"
            f"{comparison_note}"
        )
    context = "\n\n".join(
        f"[{chunk.source_id}] 标题：{chunk.title}\n路径：{chunk.path}\n内容：{chunk.text[:700]}" for chunk in chunks[:5]
    )
    context = wrap_untrusted_document(context)
    coverage_note = ""
    requested_city = _extract_requested_city(question)
    if requested_city and chunks and not any(requested_city in chunk.title or requested_city in chunk.text for chunk in chunks):
        coverage_note = (
            f"\n\n注意：用户询问的是{requested_city}，但可用资料没有直接覆盖该城市。"
            "不要用其他城市政策替代回答；应说明当前材料未覆盖，并给出可执行的转人工或核验建议。"
        )
    external_search_note = ""
    if enable_external_search:
        if _needs_external_general_advice(question):
            external_search_note = (
                "\n\n注意：这个问题是在问一般经验、准备建议、注意事项、难度或处理建议等实操信息。"
                "本地资料只用于说明公司要求、入职办理口径和必须遵守的节点；"
                "外部检索只用于补充公开参考建议。请明确区分“公司要求”和“外部建议”，"
                "不要把外部建议写成公司硬性要求。"
            )
        else:
            external_search_note = (
                "\n\n注意：这个问题需要补充外部联网检索，尤其是上一版、历史口径、最新官方口径或变化额。"
                "本地资料只作为当前项目资料参考；请优先检索政府、人社局、公积金中心、税务局等官方来源。"
                "如果外部检索结果与本地资料冲突，请说明冲突并提示人工核验。"
            )
    return f"用户问题：{question}\n\n可用资料：\n{context}{coverage_note}{external_search_note}{comparison_note}\n\n请根据以上资料回答，并在关键事实后标注引用编号。"


def _fallback_answer(question: str, chunks: list[KnowledgeChunk], llm_error: str | None = None) -> str:
    if not chunks:
        if llm_error is not None:
            reason = _human_llm_error(llm_error)
            return f"当前本地材料没有找到直接相关信息，且{reason}。建议转 HRBP 或 SSC 确认。"
        return HANDOFF_TEXT
    source = chunks[0]
    preview = source.text[:280]
    if llm_error is not None:
        reason = _human_llm_error(llm_error)
        return (
            f"我在当前材料中找到了相关信息，但{reason}，先给出检索摘要：{preview}"
            "\n\n如果你要按这个口径实际办理，建议再请 HRBP/SSC 确认一下。"
        )
    return (
        f"我在当前材料中找到了相关信息，先给出检索摘要：{preview}"
        "\n\n如果你要按这个口径实际办理，建议再请 HRBP/SSC 确认一下。"
    )


def _external_search_unavailable_answer(question: str, llm_error: str | None = None) -> str:
    reason = _human_llm_error(llm_error or "")
    return (
        f"这个问题需要外部政策检索，但{reason}；当前本地知识库不足以完整确认该城市/问题，"
        "我不能用其他城市资料替代回答，也不能用不完整资料推断政策变化。\n\n"
        "建议先通过当地人社局、公积金中心、税务局等官方渠道核验，或转 HRBP/SSC 人工确认。"
    )


def _llm_error_label(exc: Exception) -> str:
    return type(exc).__name__


def _human_llm_error(error_label: str) -> str:
    if "timeout" in error_label.lower():
        return "线上模型连接超时"
    return "自动生成暂时不可用"


def _configured_llm_provider(settings: Any) -> str:
    providers = _configured_llm_providers(settings)
    return providers[0] if providers else ""


def _configured_llm_providers(settings: Any) -> list[str]:
    provider = str(getattr(settings, "qa_llm_provider", "") or "").lower()
    fallback = _fallback_provider(settings)
    fallback_setting = str(getattr(settings, "llm_fallback_provider", "") or "").lower()
    providers: list[str] = []
    if _provider_is_configured(settings, provider):
        providers.append(provider)
    candidates = [fallback] if fallback_setting != "none" else []
    candidates.extend(["zhipu", "gemini", "deepseek"])
    for candidate in candidates:
        if candidate and candidate not in providers and _provider_is_configured(settings, candidate):
            providers.append(candidate)
    return providers


def _provider_model(settings: Any, provider: str) -> str:
    if provider == "openai":
        return str(getattr(settings, "openai_model", ""))
    if provider == "zhipu":
        return str(getattr(settings, "zhipu_text_model", ""))
    if provider == "deepseek":
        return str(getattr(settings, "deepseek_model", ""))
    if provider == "gemini_gateway":
        return f"gateway:{getattr(settings, 'gemini_gateway_url', '')}"
    return str(getattr(settings, "gemini_model", ""))


def _provider_is_configured(settings: Any, provider: str) -> bool:
    if not getattr(settings, "allow_external_ai", False):
        return False
    if provider == "openai":
        return bool(getattr(settings, "openai_api_key", "") and getattr(settings, "openai_model", ""))
    if provider == "zhipu":
        return bool(getattr(settings, "zhipu_api_key", "") and getattr(settings, "zhipu_text_model", ""))
    if provider == "gemini_gateway":
        return bool(getattr(settings, "gemini_gateway_url", "") and getattr(settings, "gemini_gateway_token", ""))
    if provider == "deepseek":
        return bool(getattr(settings, "deepseek_api_key", ""))
    if provider == "gemini":
        return bool(getattr(settings, "gemini_api_key", ""))
    return False


def _fallback_provider(settings: Any) -> str:
    provider = str(getattr(settings, "llm_fallback_provider", "") or "").lower()
    return "" if provider == "none" else provider


def _provider_supports_external_search(provider: str) -> bool:
    return provider in {"zhipu", "gemini", "gemini_gateway"}


def _source_payload(chunks: list[KnowledgeChunk], answer: str | None = None) -> list[dict[str, str]]:
    cited_ids = _cited_source_ids(answer or "")
    seen: set[str] = set()
    sources: list[dict[str, str]] = []
    for chunk in chunks:
        if cited_ids and chunk.source_id not in cited_ids:
            continue
        if chunk.source_id in seen:
            continue
        seen.add(chunk.source_id)
        sources.append({"id": chunk.source_id, "title": chunk.title, "path": chunk.path})
        if not cited_ids and len(sources) >= 4:
            break
    return sources


def _answer_sources(
    chunks: list[KnowledgeChunk],
    answer: str,
    external_sources: list[dict[str, str]],
    *,
    used_external_search: bool = False,
) -> list[dict[str, str]]:
    cited_local_sources = _source_payload(chunks, answer) if _cited_source_ids(answer) else []
    if external_sources:
        return _dedupe_sources([*external_sources, *cited_local_sources])
    if used_external_search:
        return _dedupe_sources([*_external_search_fallback_sources(), *cited_local_sources])
    return _source_payload(chunks, answer) or _source_payload(chunks)


def _dedupe_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (str(source.get("id", "")), str(source.get("path", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
        if len(deduped) >= 4:
            break
    return deduped


def _extract_zhipu_response_text(data: dict[str, Any]) -> str:
    message = ((data.get("choices") or [{}])[0].get("message") or {})
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
        return "\n".join(texts).strip()

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


def _extract_zhipu_web_search_sources(data: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if len(sources) >= 4:
            return
        if isinstance(value, dict):
            url = _source_url_from_mapping(value)
            if url and url not in seen:
                seen.add(url)
                sources.append(
                    {
                        "id": f"G{len(sources) + 1}",
                            "title": _source_title_from_mapping(value) or _source_domain(url) or "智谱联网检索来源",
                        "path": url,
                    }
                )
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return sources


def _source_url_from_mapping(value: dict[str, Any]) -> str:
    for key in ("url", "uri", "link", "href"):
        item = value.get(key)
        if isinstance(item, str) and item.startswith(("http://", "https://")):
            return item
    return ""


def _source_title_from_mapping(value: dict[str, Any]) -> str:
    for key in ("title", "name", "site_name", "source", "hostname"):
        item = value.get(key)
        if isinstance(item, str) and item.strip() and not item.startswith(("http://", "https://")):
            return item.strip()
    return ""


def _source_domain(url: str) -> str:
    return urlparse(url).netloc


def _external_search_fallback_sources() -> list[dict[str, str]]:
    return [
        {
            "id": "G1",
            "title": "外部联网检索",
            "path": "模型已使用外部联网检索生成答案，但当前响应未返回可解析来源链接。",
        }
    ]


def _extract_grounding_sources(data: dict[str, Any]) -> list[dict[str, str]]:
    candidate = (data.get("candidates") or [{}])[0] or {}
    grounding_metadata = candidate.get("groundingMetadata") or {}
    chunks = grounding_metadata.get("groundingChunks") or []
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, chunk in enumerate(chunks, start=1):
        web = chunk.get("web") or {}
        uri = web.get("uri")
        if not uri or uri in seen:
            continue
        seen.add(uri)
        title = web.get("title") or "Google Search grounding source"
        sources.append({"id": f"G{index}", "title": title, "path": uri})
    return sources[:4]


def _cited_source_ids(answer: str) -> set[str]:
    return {match.upper() for match in re.findall(r"\bS\d+\b", answer, flags=re.IGNORECASE)}


def _is_sensitive_personal_question(question: str) -> bool:
    return any(keyword in question for keyword in SENSITIVE_KEYWORDS)


def _is_clear_out_of_scope_question(question: str) -> bool:
    normalized = question.lower()
    has_tech_keyword = any(keyword in normalized for keyword in TECH_OUT_OF_SCOPE_KEYWORDS)
    if not has_tech_keyword:
        return False
    return not any(keyword in normalized for keyword in HR_SCOPE_KEYWORDS)


def _looks_like_handoff(answer: str) -> bool:
    if "转人工" in answer or "已转" in answer:
        return True
    if re.search(r"(无法|不能|不足以|没有找到).{0,18}(确认|回答|判断).{0,18}(HRBP|SSC|HR)", answer):
        return True
    return False


def _should_use_external_search(question: str, chunks: list[KnowledgeChunk]) -> bool:
    if not _is_hr_question(question):
        return False
    if _is_internal_benefits_question(question):
        return False
    if _needs_external_general_advice(question):
        return True
    if chunks and _is_file_first_question(question):
        return False
    if _is_policy_question(question) and _asks_policy_change_or_history(question):
        return True
    requested_city = _extract_requested_city(question)
    if _is_policy_question(question) and requested_city and not any(requested_city in chunk.title or requested_city in chunk.text for chunk in chunks):
        return True
    if _is_policy_question(question):
        return True
    return not chunks


def _should_include_local_chunks_with_external_search(question: str, chunks: list[KnowledgeChunk]) -> bool:
    if not chunks:
        return False
    requested_city = _extract_requested_city(question)
    if not requested_city:
        return True
    return any(requested_city in chunk.title or requested_city in chunk.text for chunk in chunks)


def _can_answer_from_local_chunks_without_search(question: str, chunks: list[KnowledgeChunk]) -> bool:
    if not chunks or _asks_policy_change_or_history(question):
        return False
    if _needs_external_general_advice(question):
        return False
    return _should_include_local_chunks_with_external_search(question, chunks)


def _asks_policy_change_or_history(question: str) -> bool:
    markers = (
        "近两年",
        "最近两年",
        "上一版",
        "上版",
        "旧版",
        "旧值",
        "新值",
        "变化",
        "变动",
        "调整",
        "上调",
        "下调",
        "同比",
        "环比",
        "去年",
        "上年",
        "往年",
        "历年",
        "历史",
        "对比",
        "2024",
        "2023",
    )
    return any(marker in question for marker in markers)


def _answer_claims_external_search(answer: str) -> bool:
    markers = ("外部检索", "外部联网检索", "联网检索", "搜索结果", "检索结果")
    return any(marker in answer for marker in markers)


def _is_policy_question(question: str) -> bool:
    return any(keyword in question for keyword in PUBLIC_POLICY_KEYWORDS)


def _is_hr_question(question: str) -> bool:
    return any(keyword in question.lower() for keyword in HR_SCOPE_KEYWORDS) or _is_policy_question(question)


def _is_internal_benefits_question(question: str) -> bool:
    if not any(keyword in question for keyword in INTERNAL_BENEFIT_KEYWORDS):
        return False
    if any(keyword in question for keyword in PUBLIC_STATUTORY_POLICY_KEYWORDS):
        return False
    return True


def _is_file_first_question(question: str) -> bool:
    if not any(keyword in question for keyword in FILE_FIRST_KEYWORDS):
        return False
    if any(keyword in question for keyword in PUBLIC_STATUTORY_POLICY_KEYWORDS):
        return False
    return True


def _needs_external_general_advice(question: str) -> bool:
    if not _is_file_first_question(question):
        return False
    if any(keyword in question for keyword in INTERNAL_REQUIREMENT_INTENT_KEYWORDS):
        return False
    return any(keyword in question for keyword in EXTERNAL_ADVICE_INTENT_KEYWORDS)


def _internal_benefit_access_block_answer(
    question: str,
    user_tags: dict[str, str | list[str] | tuple[str, ...]] | None,
    chunks: list[KnowledgeChunk],
) -> str:
    if not _is_internal_benefits_question(question):
        return ""
    if not user_tags:
        return (
            "这个问题属于公司内部福利/差旅/补贴口径。当前账号没有绑定可识别的员工身份、城市、条线或职级标签，"
            "我不能用外部搜索、行业惯例或其他公司制度替代回答。请联系 HRBP/SSC，或请 HR 先在工作台补全身份标签。"
        )
    if not _user_has_formal_employee_access(user_tags):
        return (
            "这个问题属于正式员工内部福利制度。根据当前账号身份，候选人、准员工、实习生或未转正人员不能查看具体差旅、补贴、"
            "商业保险、体检套餐或培训预算标准。转正后请以员工手册、HRBP/SSC 或正式制度通知为准。"
        )
    if not chunks:
        return (
            "这个问题属于公司内部福利制度，但当前身份标签下没有命中可查看的本地政策片段。"
            "我不能用外部搜索或其他条线/职级的制度替代回答。请联系 HRBP/SSC，或请 HR 补全城市、条线、岗位、职级标签后再查询。"
        )
    return ""


def _user_has_formal_employee_access(user_tags: dict[str, str | list[str] | tuple[str, ...]]) -> bool:
    formal_values = {"formal_employee", "regular_employee", "正式员工", "正职员工", "regular"}
    return bool((_tag_values(user_tags, "employee_status") | _tag_values(user_tags, "audience")) & formal_values)


def _tag_values(user_tags: dict[str, str | list[str] | tuple[str, ...]], key: str) -> set[str]:
    value = user_tags.get(key)
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in re.split(r"[,，、/|]", value) if item.strip()}
    return {str(item).strip() for item in value if str(item).strip()}


def _comparison_table_instruction(question: str) -> str:
    if not (_is_policy_question(question) and _asks_policy_change_or_history(question)):
        return ""
    return (
        "\n\n表格要求：用户在问政策变化、历史口径或近两年对比时，正文必须优先给一个 Markdown 表格，"
        "建议列为：项目/口径、上一连续政策年度、最新政策年度、变化、来源/说明。"
        "“近两年/最近两年”默认理解为最新两个连续政策年度；如果当前年度官方口径尚未发布，"
        "就使用已发布的最新两个连续年度并说明原因。不要跳过 2024 直接拿 2023 和 2025 对比，"
        "除非明确检索并说明 2024 官方口径缺失。表格后最多补 3 条要点。"
    )


def _extract_requested_city(question: str) -> str | None:
    for city in COMMON_POLICY_CITIES:
        if city in question:
            return city
    city_match = re.search(
        r"([\u4e00-\u9fff]{2,8}?)(?:市)?(?:的)?(?:社保|公积金|最低工资|工资标准|薪酬标准|薪资标准|缴费基数|缴存基数|医保|养老|福利|政策)",
        question,
    )
    if city_match:
        city = city_match.group(1)
        for prefix in ("请问", "查询", "想问", "了解"):
            city = city.removeprefix(prefix)
        return city[-4:] if len(city) > 4 else city
    return None
