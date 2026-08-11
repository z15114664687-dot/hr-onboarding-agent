from app.services import faq_service


def test_policy_keywords_bypass_static_faq_for_rag() -> None:
    assert faq_service.answer_question("社保怎么缴") is None
    assert faq_service.answer_question("上海公积金基数是多少") is None
    assert faq_service.answer_question("转正标准是什么") is None


def test_non_policy_static_faq_still_works() -> None:
    assert faq_service.answer_question("工资什么时候发")
