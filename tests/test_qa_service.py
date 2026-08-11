import pytest

from app.core.config import get_settings
from app.services.knowledge_base import KnowledgeChunk
from app.services.knowledge_base import load_knowledge_chunks
from app.services.qa_service import (
    _answer_sources,
    _build_prompt,
    _call_zhipu_chat,
    _clean_answer_text,
    _configured_llm_provider,
    _configured_llm_providers,
    _extract_requested_city,
    _fallback_answer,
    _extract_zhipu_web_search_sources,
    _generate_with_gemini,
    _is_internal_benefits_question,
    _is_file_first_question,
    _needs_external_general_advice,
    _llm_error_label,
    _looks_like_handoff,
    _should_include_local_chunks_with_external_search,
    _should_use_external_search,
    answer_hr_question,
    format_qa_reply,
    QAResult,
)


@pytest.mark.asyncio
async def test_qa_sensitive_salary_question_handoffs() -> None:
    result = await answer_hr_question("我的薪资是多少")

    assert result.needs_handoff is True
    assert "HRBP" in result.answer


@pytest.mark.asyncio
async def test_qa_rejects_clear_non_hr_technical_question(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    get_settings.cache_clear()

    result = await answer_hr_question("infra以后可能也不会局限于gpu跟CUDA，解释一下为啥这样讲")

    assert result.needs_handoff is False
    assert result.sources == []
    assert "不属于入职流程、HR政策或材料办理范围" in result.answer


@pytest.mark.asyncio
async def test_qa_uses_local_rag_without_gemini(tmp_path, monkeypatch) -> None:
    source = tmp_path / "policy.md"
    source.write_text("北京住房公积金月缴存基数上限为35811元，下限为2540元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    result = await answer_hr_question("北京公积金基数是多少")

    assert result.needs_handoff is False
    assert "35811" in result.answer
    assert result.sources


@pytest.mark.asyncio
async def test_qa_uses_deepseek_provider(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "policy.md"
    source.write_text("北京最低工资标准为每月2540元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_deepseek(question, chunks):
        assert question == "北京最低工资是多少"
        assert chunks
        return "北京最低工资标准为每月2540元 [S1]。", []

    monkeypatch.setattr(qa_service, "_generate_with_deepseek", fake_deepseek)

    result = await answer_hr_question("北京最低工资是多少")

    assert result.answer == "北京最低工资标准为每月2540元 [S1]。"
    assert result.sources == [{"id": "S1", "title": "policy", "path": str(source)}]
    assert result.used_external_search is False


@pytest.mark.asyncio
async def test_regularization_question_uses_local_docs_without_external_search(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "转正手续办理指引.md"
    source.write_text("转正标准包括完成试用期考察、直属经理确认和 OA 转正流程。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_ENABLE_GOOGLE_SEARCH", "true")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_gateway(question, chunks, *, enable_external_search=False):
        assert question == "转正标准是什么"
        assert chunks
        assert enable_external_search is False
        return "转正标准包括完成试用期考察、直属经理确认和 OA 转正流程 [S1]。", []

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", fake_gateway)

    result = await answer_hr_question("转正标准是什么")

    assert result.used_external_search is False
    assert result.sources == [{"id": "S1", "title": "转正手续办理指引", "path": str(source)}]
    assert "OA 转正流程" in result.answer


@pytest.mark.asyncio
async def test_fund_exam_prep_advice_uses_external_search_with_local_context(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "岗位资格资格说明.md"
    source.write_text("必须取得岗位资格资格的岗位，应在入职前通过考试；未通过的应在入职后参加首次考试。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_gateway(question, chunks, *, enable_external_search=False):
        assert question == "岗位资格准备多久比较好"
        assert chunks
        assert chunks[0].title == "岗位资格资格说明"
        assert enable_external_search is True
        return (
            "公司要求是相关岗位需通过岗位资格考试；备考建议可按个人基础安排，金融背景通常 1-2 周，"
            "非金融背景可预留 1-2 个月 [S1]。外部备考建议不等同于公司硬性要求。",
            [{"id": "G1", "title": "公开备考经验", "path": "https://example.com/fund-exam"}],
        )

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", fake_gateway)

    result = await answer_hr_question("岗位资格准备多久比较好")

    assert result.used_external_search is True
    assert "公司要求" in result.answer
    assert "备考建议" in result.answer
    assert {"id": "S1", "title": "岗位资格资格说明", "path": str(source)} in result.sources
    assert {"id": "G1", "title": "公开备考经验", "path": "https://example.com/fund-exam"} in result.sources


@pytest.mark.asyncio
async def test_salary_standard_query_uses_local_rag_without_gemini(tmp_path, monkeypatch) -> None:
    source = tmp_path / "北京_2025最低工资标准.md"
    source.write_text("北京市最低工资标准：每月不低于2540元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    result = await answer_hr_question("北京的薪酬标准是什么")

    assert result.needs_handoff is False
    assert "2540" in result.answer
    assert result.sources == [{"id": "S1", "title": "北京_2025最低工资标准", "path": str(source)}]


@pytest.mark.asyncio
async def test_qa_falls_back_from_gateway_to_deepseek(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "policy.md"
    source.write_text("北京最低工资标准为每月2540元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "deepseek")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def failing_gateway(question, chunks, *, enable_external_search=False):
        raise TimeoutError()

    async def fake_deepseek(question, chunks):
        return "北京最低工资标准为每月2540元 [S1]。", []

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", failing_gateway)
    monkeypatch.setattr(qa_service, "_generate_with_deepseek", fake_deepseek)

    result = await answer_hr_question("北京最低工资是多少")

    assert result.answer == "北京最低工资标准为每月2540元 [S1]。"
    assert result.sources == [{"id": "S1", "title": "policy", "path": str(source)}]


@pytest.mark.asyncio
async def test_qa_falls_back_from_gateway_to_zhipu(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "policy.md"
    source.write_text("北京最低工资标准为每月2540元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "zhipu")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("ZHIPU_TEXT_MODEL", "glm-4.5-air")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def failing_gateway(question, chunks, *, enable_external_search=False):
        raise TimeoutError()

    async def fake_zhipu(question, chunks, *, enable_web_search=False):
        assert enable_web_search is True
        assert chunks
        return "北京最低工资标准为每月2540元 [S1]。", []

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", failing_gateway)
    monkeypatch.setattr(qa_service, "_generate_with_zhipu", fake_zhipu)

    result = await answer_hr_question("北京最低工资是多少")

    assert result.answer == "北京最低工资标准为每月2540元 [S1]。"
    assert result.sources == [
        {
            "id": "G1",
            "title": "外部联网检索",
            "path": "模型已使用外部联网检索生成答案，但当前响应未返回可解析来源链接。",
        },
        {"id": "S1", "title": "policy", "path": str(source)},
    ]


@pytest.mark.asyncio
async def test_external_search_without_grounding_sources_does_not_reuse_local_sources(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "policy.md"
    source.write_text("上海最低工资标准为每月2740元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_gateway(question, chunks, *, enable_external_search=False):
        assert question == "哈尔滨最低工资标准"
        assert chunks == []
        assert enable_external_search is True
        return "外部检索显示，哈尔滨最低工资标准需以黑龙江省人社部门口径为准。", []

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", fake_gateway)

    result = await answer_hr_question("哈尔滨最低工资标准")

    assert result.used_external_search is True
    assert result.sources == [
        {
            "id": "G1",
            "title": "外部联网检索",
            "path": "模型已使用外部联网检索生成答案，但当前响应未返回可解析来源链接。",
        }
    ]


@pytest.mark.asyncio
async def test_candidate_internal_benefit_question_is_blocked_without_external_search(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "formal_travel.md"
    source.write_text(
        """---
audience: formal_employee
employee_status: 正式员工
sensitivity: internal_benefits
---

正式员工差旅住宿标准为一线城市 850 元/晚。
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def unexpected_gateway(*args, **kwargs):
        raise AssertionError("candidate internal benefit question must not call external-capable LLM")

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", unexpected_gateway)

    result = await answer_hr_question(
        "待入职实习生能看差旅住宿标准吗",
        user_tags={"employee_status": ["candidate", "准员工", "候选人"], "department": "产品与技术"},
    )

    assert result.needs_handoff is True
    assert result.used_external_search is False
    assert result.sources == []
    assert "候选人、准员工、实习生或未转正人员不能查看" in result.answer
    assert "850" not in result.answer


@pytest.mark.asyncio
async def test_formal_employee_internal_benefit_uses_local_only(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "formal_travel.md"
    source.write_text(
        """---
audience: formal_employee
employee_status: 正式员工
sensitivity: internal_benefits
---

正式员工差旅住宿标准为一线城市 850 元/晚。
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_gateway(question, chunks, *, enable_external_search=False):
        assert question == "正式员工差旅住宿标准"
        assert enable_external_search is False
        assert chunks
        return "正式员工差旅住宿标准为一线城市 850 元/晚 [S1]。", []

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", fake_gateway)

    result = await answer_hr_question(
        "正式员工差旅住宿标准",
        user_tags={"employee_status": "formal_employee", "department": "产品与技术"},
    )

    assert result.needs_handoff is False
    assert result.used_external_search is False
    assert "850" in result.answer
    assert result.sources == [{"id": "S1", "title": "formal_travel", "path": str(source)}]


@pytest.mark.asyncio
async def test_formal_employee_internal_benefit_without_matching_tags_is_blocked(tmp_path, monkeypatch) -> None:
    source = tmp_path / "formal_investment.md"
    source.write_text(
        """---
audience: formal_employee
employee_status: 正式员工
department: 产品与技术
sensitivity: internal_benefits
---

产品与技术专家访谈支持单项目上限 20000 元。
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    result = await answer_hr_question(
        "产品与技术专家访谈支持是多少",
        user_tags={"employee_status": "formal_employee", "department": "销售条线"},
    )

    assert result.needs_handoff is True
    assert result.used_external_search is False
    assert result.sources == []
    assert "不能用外部搜索或其他条线/职级的制度替代回答" in result.answer
    assert "20000" not in result.answer


@pytest.mark.asyncio
async def test_external_search_can_use_zhipu_web_search(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "policy.md"
    source.write_text("上海最低工资标准为每月2740元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "zhipu")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("ZHIPU_TEXT_MODEL", "glm-4.5-air")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_zhipu(question, chunks, *, enable_web_search=False):
        assert question == "哈尔滨最低工资标准"
        assert chunks == []
        assert enable_web_search is True
        return "联网检索显示，哈尔滨最低工资标准需以黑龙江省人社部门口径为准。", []

    monkeypatch.setattr(qa_service, "_generate_with_zhipu", fake_zhipu)

    result = await answer_hr_question("哈尔滨最低工资标准")

    assert result.used_external_search is True
    assert result.sources == [
        {
            "id": "G1",
            "title": "外部联网检索",
            "path": "模型已使用外部联网检索生成答案，但当前响应未返回可解析来源链接。",
        }
    ]


@pytest.mark.asyncio
async def test_policy_change_question_uses_external_search_with_local_city_chunks(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "shanghai_policy.md"
    source.write_text("上海社保缴费基数 2025 年下限为7460元，上限为37302元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_gateway(question, chunks, *, enable_external_search=False):
        assert question == "上海社保缴费基数和上一版相比有什么变化"
        assert chunks
        assert chunks[0].title == "shanghai_policy"
        assert enable_external_search is True
        return "本地资料显示 2025 年上海社保缴费基数为下限7460元、上限37302元 [S1]；上一版需以外部官方检索为准。", []

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", fake_gateway)

    result = await answer_hr_question("上海社保缴费基数和上一版相比有什么变化")

    assert result.used_external_search is True
    assert result.sources[0]["title"] == "外部联网检索"
    assert any(source["title"] == "shanghai_policy" for source in result.sources)


@pytest.mark.asyncio
async def test_external_search_failure_does_not_fall_back_to_unrelated_local_answer(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "policy.md"
    source.write_text("上海最低工资标准为每月2740元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "deepseek")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def failing_gateway(question, chunks, *, enable_external_search=False):
        raise TimeoutError()

    async def fake_deepseek(question, chunks):
        return "上海最低工资标准为每月2740元 [S1]。", []

    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", failing_gateway)
    monkeypatch.setattr(qa_service, "_generate_with_deepseek", fake_deepseek)

    result = await answer_hr_question("哈尔滨最低工资标准")

    assert result.needs_handoff is True
    assert result.sources == []
    assert "不能用其他城市资料替代回答" in result.answer
    assert "上海最低工资" not in result.answer


def test_extract_requested_city_for_policy_questions() -> None:
    assert _extract_requested_city("武汉的社保缴费基数") == "武汉"
    assert _extract_requested_city("北京的福利标准") == "北京"


def test_external_search_needed_when_city_not_in_local_chunks(tmp_path) -> None:
    source = tmp_path / "policy.md"
    source.write_text("北京社保缴费基数上限为35811元。", encoding="utf-8")
    load_knowledge_chunks.cache_clear()
    chunks = list(load_knowledge_chunks(str(tmp_path)))

    assert _should_use_external_search("武汉的社保缴费基数", chunks) is True
    assert _should_use_external_search("北京的社保缴费基数", chunks) is True
    assert _should_use_external_search("北京社保缴费基数比上一版变化", chunks) is True
    assert _should_use_external_search("香港的薪酬标准", chunks) is True
    assert _should_use_external_search("入职材料有哪些", []) is True


def test_file_first_questions_use_local_chunks_without_external_search() -> None:
    chunks = [KnowledgeChunk("S1", "转正手续办理指引", "/tmp/regularization.md", "转正条件包括完成培训和试用期考核。")]

    assert _is_file_first_question("转正标准是什么") is True
    assert _is_file_first_question("入职材料有哪些") is True
    assert _should_use_external_search("转正标准是什么", chunks) is False
    assert _should_use_external_search("入职材料有哪些", chunks) is False
    assert _should_use_external_search("上海社保缴费基数", chunks) is True


def test_general_advice_can_use_external_search_with_local_chunks() -> None:
    chunks = [KnowledgeChunk("S1", "岗位资格资格说明", "/tmp/fund.md", "需取得岗位资格资格的岗位，应在入职前通过考试。")]

    assert _is_file_first_question("岗位资格准备多久比较好") is True
    assert _needs_external_general_advice("岗位资格准备多久比较好") is True
    assert _should_use_external_search("岗位资格准备多久比较好", chunks) is True
    assert _should_include_local_chunks_with_external_search("岗位资格准备多久比较好", chunks) is True
    assert _needs_external_general_advice("体检前有什么注意事项") is True
    assert _needs_external_general_advice("背调一般会查什么") is True
    assert _should_use_external_search("体检前有什么注意事项", chunks) is True
    assert _should_use_external_search("背调一般会查什么", chunks) is True
    assert _needs_external_general_advice("入职材料有哪些") is False
    assert _needs_external_general_advice("转正标准是什么") is False


def test_internal_benefit_questions_do_not_trigger_external_search() -> None:
    assert _is_internal_benefits_question("正式员工差旅住宿标准") is True
    assert _should_use_external_search("正式员工差旅住宿标准", []) is False
    assert _is_internal_benefits_question("上海社保缴费基数") is False


def test_qa_prompt_warns_when_requested_city_not_covered() -> None:
    chunk = KnowledgeChunk("S1", "上海_2025最低工资标准", "/tmp/shanghai.md", "上海最低工资标准为2740元。")

    prompt = _build_prompt("哈尔滨最低工资是多少", [chunk])

    assert "哈尔滨" in prompt
    assert "不要用其他城市政策替代回答" in prompt


def test_qa_prompt_for_external_search_keeps_local_context() -> None:
    chunk = KnowledgeChunk("S1", "上海_2025社保缴费基数", "/tmp/shanghai.md", "上海社保缴费基数 2025 年下限为7460元。")

    prompt = _build_prompt("上海社保缴费基数和上一版相比有什么变化", [chunk], enable_external_search=True)

    assert "可用资料" in prompt
    assert "补充外部联网检索" in prompt
    assert "上一版" in prompt


def test_qa_prompt_for_exam_advice_distinguishes_company_requirements_and_external_advice() -> None:
    chunk = KnowledgeChunk("S1", "岗位资格资格说明", "/tmp/fund.md", "需取得岗位资格资格的岗位，应在入职前通过考试。")

    prompt = _build_prompt("岗位资格准备多久比较好", [chunk], enable_external_search=True)

    assert "公司要求" in prompt
    assert "外部建议" in prompt
    assert "不要把外部建议写成公司硬性要求" in prompt


def test_qa_prompt_for_recent_two_years_requires_consecutive_table() -> None:
    chunk = KnowledgeChunk("S1", "武汉_2025社保缴费基数", "/tmp/wuhan.md", "武汉社保缴费基数 2025 年下限为4498元。")

    prompt = _build_prompt("武汉的社保标准近两年对比", [chunk], enable_external_search=True)

    assert "Markdown 表格" in prompt
    assert "最新两个连续政策年度" in prompt
    assert "不要跳过 2024" in prompt


def test_qa_fallback_reports_empty_timeout_error() -> None:
    chunk = KnowledgeChunk("S1", "政策", "/tmp/policy.md", "上海最低工资标准为2740元。")

    answer = _fallback_answer("上海最低工资", [chunk], llm_error="ConnectTimeout")

    assert "线上模型连接超时" in answer
    assert "2740" in answer


def test_qa_fallback_handles_empty_chunks_after_llm_error() -> None:
    answer = _fallback_answer("哈尔滨福利标准", [], llm_error="ConnectTimeout")

    assert "本地材料没有找到直接相关信息" in answer
    assert "线上模型连接超时" in answer


@pytest.mark.asyncio
async def test_gemini_empty_stop_retries_once(monkeypatch) -> None:
    import app.services.qa_service as qa_service

    calls: list[str] = []

    async def fake_call_gemini(model, api_key, prompt, enable_google_search, **kwargs):
        assert enable_google_search is False
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("Gemini returned empty answer finish_reason=STOP")
        return "北京福利标准按本地政策执行 [S1]。", []

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GEMINI_ENABLE_GOOGLE_SEARCH", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(qa_service, "_call_gemini", fake_call_gemini)

    answer, sources = await _generate_with_gemini(
        "北京福利标准是什么",
        [KnowledgeChunk("S1", "北京福利政策", "/tmp/policy.md", "北京福利标准按本地政策执行。")],
    )

    assert answer == "北京福利标准按本地政策执行 [S1]。"
    assert sources == []
    assert len(calls) == 2
    assert "不要留空" in calls[1]


def test_qa_llm_error_label_keeps_exception_type_for_empty_detail() -> None:
    assert _llm_error_label(TimeoutError()) == "TimeoutError"


def test_qa_clean_answer_text_removes_markdown_markers() -> None:
    assert _clean_answer_text("**重点**\n* 第一条\n*温馨提示*") == "重点\n- 第一条\n温馨提示"


def test_qa_policy_double_check_is_not_handoff() -> None:
    assert _looks_like_handoff("建议与 HRBP/SSC 或最新官方口径 double check。") is False
    assert _looks_like_handoff("当前无法确认，已转人工处理。") is True


def test_answer_sources_falls_back_when_model_miscites_source() -> None:
    chunks = [KnowledgeChunk("S1", "转正手续办理指引", "/tmp/regularization.md", "转正条件包括完成培训。")]

    sources = _answer_sources(chunks, "转正条件包括完成培训 [S99]。", [], used_external_search=False)

    assert sources == [{"id": "S1", "title": "转正手续办理指引", "path": "/tmp/regularization.md"}]


def test_answer_sources_combines_external_with_cited_local_sources() -> None:
    chunks = [
        KnowledgeChunk("S1", "员工福利制度", "/tmp/benefits.md", "公司补充福利以本地制度为准。"),
        KnowledgeChunk("S2", "上海_2025最低工资标准", "/tmp/shanghai.md", "上海最低工资标准为2740元。"),
    ]
    external_sources = [{"id": "G1", "title": "香港劳工处", "path": "https://www.labour.gov.hk/"}]

    sources = _answer_sources(
        chunks,
        "公司补充福利参见本地制度 [S1]；香港最低工资按香港劳工处外部检索结果核验。",
        external_sources,
        used_external_search=True,
    )

    assert sources == [
        {"id": "G1", "title": "香港劳工处", "path": "https://www.labour.gov.hk/"},
        {"id": "S1", "title": "员工福利制度", "path": "/tmp/benefits.md"},
    ]


@pytest.mark.asyncio
async def test_external_search_claim_does_not_attach_unrelated_local_sources(tmp_path, monkeypatch) -> None:
    import app.services.qa_service as qa_service

    source = tmp_path / "上海_2025最低工资标准.md"
    source.write_text("上海最低工资标准为每月2740元。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    load_knowledge_chunks.cache_clear()

    async def fake_gateway(question, chunks, *, enable_external_search=False):
        return "根据香港特区政府劳工处规定（外部检索结果），香港法定最低工资为每小时 43.1 港元。", []

    monkeypatch.setattr(qa_service, "_should_use_external_search", lambda question, chunks: False)
    monkeypatch.setattr(qa_service, "_generate_with_gemini_gateway", fake_gateway)

    result = await answer_hr_question("香港的薪酬标准")

    assert result.used_external_search is True
    assert result.sources == [
        {
            "id": "G1",
            "title": "外部联网检索",
            "path": "模型已使用外部联网检索生成答案，但当前响应未返回可解析来源链接。",
        }
    ]


def test_qa_configured_llm_provider_prefers_requested_provider(monkeypatch) -> None:
    monkeypatch.setenv("QA_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    get_settings.cache_clear()

    assert _configured_llm_provider(get_settings()) == "deepseek"


def test_qa_configured_llm_providers_include_gateway_and_fallback(monkeypatch) -> None:
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "deepseek")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    get_settings.cache_clear()

    assert _configured_llm_providers(get_settings()) == ["gemini_gateway", "deepseek"]


def test_qa_configured_llm_providers_respect_deepseek_fallback_before_zhipu(monkeypatch) -> None:
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "deepseek")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    get_settings.cache_clear()

    assert _configured_llm_providers(get_settings()) == ["gemini_gateway", "deepseek", "zhipu"]


def test_qa_configured_llm_providers_include_zhipu_fallback(monkeypatch) -> None:
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "zhipu")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()

    assert _configured_llm_providers(get_settings()) == ["gemini_gateway", "zhipu"]


def test_qa_configured_llm_providers_use_zhipu_when_deepseek_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("QA_LLM_PROVIDER", "gemini_gateway")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "deepseek")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()

    assert _configured_llm_providers(get_settings()) == ["gemini_gateway", "zhipu"]


@pytest.mark.asyncio
async def test_zhipu_chat_uses_web_search_tool(monkeypatch) -> None:
    import app.services.qa_service as qa_service

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"output_text": "哈尔滨最低工资标准需以官方口径为准。"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(qa_service.httpx, "AsyncClient", FakeAsyncClient)

    answer, sources = await _call_zhipu_chat(
        "glm-4.5-air",
        "zhipu-key",
        "用户问题：哈尔滨最低工资标准",
        enable_web_search=True,
    )

    assert answer == "哈尔滨最低工资标准需以官方口径为准。"
    assert sources == []
    assert captured["url"].endswith("/chat/completions")
    assert captured["json"]["tools"][0]["type"] == "web_search"
    assert captured["json"]["tools"][0]["web_search"]["enable"] is True


def test_extract_zhipu_web_search_sources_from_nested_response() -> None:
    sources = _extract_zhipu_web_search_sources(
        {
            "output": [
                {
                    "type": "web_search_call",
                    "results": [
                        {
                            "title": "哈尔滨市人力资源和社会保障局",
                            "url": "https://hrb.example.gov.cn/minimum-wage",
                        },
                        {
                            "title": "重复来源",
                            "url": "https://hrb.example.gov.cn/minimum-wage",
                        },
                    ],
                }
            ]
        }
    )

    assert sources == [
        {
            "id": "G1",
            "title": "哈尔滨市人力资源和社会保障局",
            "path": "https://hrb.example.gov.cn/minimum-wage",
        }
    ]


def test_format_qa_reply_keeps_external_search_source_note_without_links() -> None:
    text = format_qa_reply(QAResult(answer="英国福利待遇以当地官方口径为准。", sources=[], used_external_search=True))

    assert "外部联网检索" in text
    assert "当前响应未返回可解析来源链接" in text
