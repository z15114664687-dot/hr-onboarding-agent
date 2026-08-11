from __future__ import annotations

FAQ_ANSWERS: dict[str, str] = {
    "薪资": "薪资信息请以 offer 和 HR 确认为准；如果你想查询个人薪资，我这里不能直接确认，建议转 HRBP 或 SSC。",
    "工资": "月度薪酬一般在每月 10 日发放上月薪酬；如果遇到法定节假日或休息日，通常会顺延到最近的工作日。",
    "企业年金": "企业年金通常在试用期结束并完成转正程序后开始安排缴纳，具体以 HR 通知为准。",
}

RAG_FIRST_KEYWORDS = (
    "社保",
    "公积金",
    "最低工资",
    "政策",
    "标准",
    "缴费基数",
    "缴存基数",
    "转正",
    "岗位资格",
    "证券账户",
    "党组织",
    "团组织",
    "体检",
)


def answer_question(text: str) -> str | None:
    """Return a simple keyword FAQ answer, or None when no match is found."""

    if should_use_rag(text):
        return None
    for keyword, answer in FAQ_ANSWERS.items():
        if keyword in text:
            return answer
    return None


def should_use_rag(text: str) -> bool:
    """Return whether a question should bypass static FAQ and use RAG."""

    return any(keyword in text for keyword in RAG_FIRST_KEYWORDS)
