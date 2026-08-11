from functools import lru_cache
import ipaddress
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    allow_external_ai: bool = Field(default=False, alias="ALLOW_EXTERNAL_AI")
    allow_private_provider_urls: bool = Field(default=False, alias="ALLOW_PRIVATE_PROVIDER_URLS")
    store_event_payloads: bool = Field(default=False, alias="STORE_EVENT_PAYLOADS")
    store_raw_ocr_text: bool = Field(default=False, alias="STORE_RAW_OCR_TEXT")
    hr_admin_username: str = Field(default="admin", alias="HR_ADMIN_USERNAME")
    hr_admin_password: str = Field(default="", alias="HR_ADMIN_PASSWORD")
    app_session_secret: str = Field(default="", alias="APP_SESSION_SECRET")
    candidate_token_ttl_seconds: int = Field(default=2_592_000, alias="CANDIDATE_TOKEN_TTL_SECONDS")
    feishu_max_event_age_seconds: int = Field(default=300, alias="FEISHU_MAX_EVENT_AGE_SECONDS")
    upload_root: str = Field(default="uploads/materials", alias="UPLOAD_ROOT")
    max_upload_bytes: int = Field(default=12 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    feishu_verification_token: str = Field(default="", alias="FEISHU_VERIFICATION_TOKEN")
    feishu_encrypt_key: str = Field(default="", alias="FEISHU_ENCRYPT_KEY")
    feishu_bot_app_name: str = Field(default="HR助理", alias="FEISHU_BOT_APP_NAME")
    feishu_base_app_token: str = Field(default="", alias="FEISHU_BASE_APP_TOKEN")
    feishu_candidate_table_id: str = Field(default="", alias="FEISHU_CANDIDATE_TABLE_ID")
    feishu_node_table_id: str = Field(default="", alias="FEISHU_NODE_TABLE_ID")
    feishu_material_table_id: str = Field(default="", alias="FEISHU_MATERIAL_TABLE_ID")
    feishu_faq_table_id: str = Field(default="", alias="FEISHU_FAQ_TABLE_ID")
    database_url: str = Field(default="sqlite:///./local.db", alias="DATABASE_URL")
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")
    feishu_web_app_base_url: str = Field(default="", alias="FEISHU_WEB_APP_BASE_URL")
    feishu_hr_dashboard_url: str = Field(default="", alias="FEISHU_HR_DASHBOARD_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    knowledge_base_dir: str = Field(default="examples/knowledge_base", alias="KNOWLEDGE_BASE_DIR")
    workflow_config_path: str = Field(default="examples/workflows/default.yaml", alias="WORKFLOW_CONFIG_PATH")
    llm_provider: Literal["mock", "openai", "gemini", "ark", "zhipu", "deepseek"] = Field(
        default="mock", alias="LLM_PROVIDER"
    )
    ocr_provider: Literal["mock", "openai", "gemini", "ark"] = Field(default="mock", alias="OCR_PROVIDER")
    qa_llm_provider: Literal["mock", "openai", "zhipu", "gemini", "deepseek", "gemini_gateway"] = Field(
        default="mock", alias="QA_LLM_PROVIDER"
    )
    llm_fallback_provider: Literal["none", "mock", "openai", "zhipu", "gemini", "deepseek"] = Field(
        default="none", alias="LLM_FALLBACK_PROVIDER"
    )
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5-mini", alias="OPENAI_MODEL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_timeout_seconds: float = Field(default=60.0, alias="OPENAI_TIMEOUT_SECONDS")
    zhipu_api_key: str = Field(default="", alias="ZHIPU_API_KEY")
    zhipu_text_model: str = Field(default="glm-4.5-air", alias="ZHIPU_TEXT_MODEL")
    zhipu_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", alias="ZHIPU_BASE_URL")
    zhipu_timeout_seconds: float = Field(default=120.0, alias="ZHIPU_TIMEOUT_SECONDS")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_enable_google_search: bool = Field(default=False, alias="GEMINI_ENABLE_GOOGLE_SEARCH")
    gemini_proxy_url: str = Field(default="", alias="GEMINI_PROXY_URL")
    gemini_timeout_seconds: float = Field(default=120.0, alias="GEMINI_TIMEOUT_SECONDS")
    gemini_gateway_url: str = Field(default="", alias="GEMINI_GATEWAY_URL")
    gemini_gateway_token: str = Field(default="", alias="GEMINI_GATEWAY_TOKEN")
    gemini_gateway_timeout_seconds: float = Field(default=120.0, alias="GEMINI_GATEWAY_TIMEOUT_SECONDS")
    ark_api_key: str = Field(default="", alias="ARK_API_KEY")
    ark_model: str = Field(default="doubao-seed-2-0-lite-260428", alias="ARK_MODEL")
    ark_base_url: str = Field(default="https://ark.cn-beijing.volces.com/api/v3", alias="ARK_BASE_URL")
    ark_timeout_seconds: float = Field(default=120.0, alias="ARK_TIMEOUT_SECONDS")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_timeout_seconds: float = Field(default=30.0, alias="DEEPSEEK_TIMEOUT_SECONDS")
    environment: Literal["local", "test", "prod"] = "local"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        """Reject dangerous provider endpoints and incomplete production auth."""

        if self.environment == "prod" and not self.demo_mode:
            if len(self.app_session_secret) < 32:
                raise ValueError("APP_SESSION_SECRET must contain at least 32 characters in production")
            if len(self.hr_admin_password) < 12:
                raise ValueError("HR_ADMIN_PASSWORD must contain at least 12 characters in production")
            if self.ocr_provider == "mock" or self.llm_provider == "mock":
                raise ValueError("mock providers are allowed only outside production or when DEMO_MODE=true")

        if self.environment != "test" and not self.allow_private_provider_urls:
            for field_name in (
                "openai_base_url",
                "zhipu_base_url",
                "ark_base_url",
                "deepseek_base_url",
                "gemini_proxy_url",
                "gemini_gateway_url",
            ):
                value = str(getattr(self, field_name, "") or "").strip()
                if value:
                    _validate_public_https_url(field_name, value)
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached process-wide settings."""

    return Settings()


def _validate_public_https_url(field_name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{field_name} must use an https URL")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError(f"{field_name} cannot target a local host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError(f"{field_name} cannot target a private or non-global IP address")


CANDIDATE_FIELD_MAPPING: dict[str, str] = {
    "candidate_name": "候选人姓名",
    "mobile": "手机号",
    "email": "邮箱",
    "feishu_open_id": "飞书OpenID",
    "department": "部门",
    "position": "岗位",
    "employment_type": "招聘类型",
    "job_level": "职级",
    "city": "城市",
    "hrbp_open_id": "HRBP",
    "manager_open_id": "直属经理",
    "expected_onboard_date": "预计入职日期",
    "current_stage": "当前阶段",
    "current_node": "当前节点",
    "next_due_date": "下一截止日期",
    "overdue_node_count": "超时节点数",
    "material_progress": "材料进度",
    "overall_status": "整体状态",
    "risk_level": "风险等级",
}

NODE_FIELD_MAPPING: dict[str, str] = {
    "case_id": "候选人ID",
    "node_group": "节点分组",
    "node_code": "节点编码",
    "node_name": "节点名称",
    "owner_open_id": "负责人OpenID",
    "collaborator_open_id": "协同人OpenID",
    "due_date": "截止日期",
    "completed_at": "完成时间",
    "status": "状态",
    "reminder_status": "提醒状态",
    "overdue_days": "超时天数",
    "reminder_count": "催办次数",
    "escalation_level": "升级等级",
}

MATERIAL_FIELD_MAPPING: dict[str, str] = {
    "case_id": "候选人ID",
    "candidate_name": "候选人姓名",
    "material_type": "材料类型",
    "material_code": "材料编码",
    "employment_type": "适用用工类型",
    "file_name": "文件名",
    "file_token": "文件Token",
    "ocr_text": "OCR文本",
    "extracted_fields_json": "提取字段JSON",
    "review_status": "审核状态",
    "submission_status": "提交状态",
    "review_reason": "审核原因",
    "candidate_feedback": "候选人反馈",
    "retry_count": "重交次数",
    "submitted_at": "最近提交时间",
}
