from __future__ import annotations

BUSINESS_LINE_POSITIONS: dict[str, tuple[str, ...]] = {
    "产品与技术": (
        "产品管理",
        "后端工程",
        "前端工程",
        "数据工程",
        "AI 应用",
        "质量保障",
        "开发者关系",
        "安全工程",
    ),
    "业务运营": ("客户支持", "项目运营", "内容运营", "社区运营"),
    "组织支持": (
        "People Operations",
        "IT 支持",
        "数据治理",
        "战略与研究",
        "商务运营",
        "财务运营",
        "行政运营",
    ),
    "客户成功": ("客户成功",),
}

DEPARTMENT_OPTIONS = tuple(BUSINESS_LINE_POSITIONS.keys())
POSITION_OPTIONS = tuple(position for positions in BUSINESS_LINE_POSITIONS.values() for position in positions)
EMPLOYMENT_TYPE_OPTIONS = ("社招", "校招-境内", "校招-境外")
