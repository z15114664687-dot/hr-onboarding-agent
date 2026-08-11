from app.core.config import get_settings
from app.utils.feishu_links import candidate_materials_url, candidate_page_url, hr_dashboard_url, web_app_home_url


def test_feishu_links_use_web_app_base_url(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("FEISHU_WEB_APP_BASE_URL", "https://demo.example.com")
    monkeypatch.setenv("FEISHU_HR_DASHBOARD_URL", "")
    get_settings.cache_clear()
    try:
        assert web_app_home_url() == "https://demo.example.com/ui"
        assert candidate_page_url(12) == "https://demo.example.com/ui/candidate/12/workspace"
        assert candidate_materials_url(12) == "https://demo.example.com/ui/candidate/12/workspace#materials"
        assert hr_dashboard_url() == "http://localhost:8000/ui/hr/workspace"
    finally:
        get_settings.cache_clear()


def test_hr_dashboard_link_uses_web_workspace_even_when_bitable_dashboard_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://hr-onboarding.example.com")
    monkeypatch.setenv("FEISHU_WEB_APP_BASE_URL", "https://demo.example.com")
    monkeypatch.setenv("FEISHU_HR_DASHBOARD_URL", "https://example.feishu.cn/base/dashboard")
    get_settings.cache_clear()
    try:
        assert hr_dashboard_url() == "https://hr-onboarding.example.com/ui/hr/workspace"
    finally:
        get_settings.cache_clear()
