from __future__ import annotations

from urllib.parse import urlencode

from app.core.config import get_settings
from app.core.security import issue_candidate_token


def hr_dashboard_url() -> str:
    """Return the HR workspace URL for Feishu card buttons."""

    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}/ui/hr/workspace"


def candidate_page_url(case_id: int) -> str:
    """Return the preferred candidate-facing H5 workspace URL."""

    base = f"{_web_base_url()}/ui/candidate/{case_id}/workspace"
    if get_settings().demo_mode:
        return base
    return f"{base}?{urlencode({'access_token': issue_candidate_token(case_id)})}"


def candidate_materials_url(case_id: int) -> str:
    """Return the preferred candidate-facing material section URL."""

    return f"{candidate_page_url(case_id)}#materials"


def candidate_node_confirm_url(case_id: int, node_name: str) -> str:
    """Return a candidate page URL that opens the node confirmation prompt."""

    page_url = candidate_page_url(case_id)
    separator = "&" if "?" in page_url else "?"
    return f"{page_url}{separator}{urlencode({'confirm_node': node_name})}#node-{node_name}"


def web_app_home_url() -> str:
    """Return the web app home URL that should be configured in Feishu."""

    return f"{_web_base_url()}/ui"


def _web_base_url() -> str:
    settings = get_settings()
    return (settings.feishu_web_app_base_url or settings.public_base_url).rstrip("/")
