import pytest

from app.adapters import feishu_im


class FakeResponse:
    def __init__(self, data: dict, *, status_code: int = 200, text: str = "") -> None:
        self._data = data
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


@pytest.mark.asyncio
async def test_send_message_raises_on_feishu_business_error(monkeypatch) -> None:
    async def fake_token() -> str:
        return "tenant-token"

    async def fake_request_with_retry(*args, **kwargs) -> FakeResponse:
        return FakeResponse({"code": 230001, "msg": "bad receiver"})

    monkeypatch.setattr(feishu_im, "get_tenant_access_token", fake_token)
    monkeypatch.setattr(feishu_im, "request_with_retry", fake_request_with_retry)

    with pytest.raises(feishu_im.FeishuMessageError, match="code=230001"):
        await feishu_im.send_text("ou_missing", "hello")


@pytest.mark.asyncio
async def test_send_message_redacts_http_error_body(monkeypatch) -> None:
    async def fake_token() -> str:
        return "tenant-token"

    async def fake_request_with_retry(*args, **kwargs) -> FakeResponse:
        return FakeResponse({}, status_code=400, text='{"code":230099,"msg":"bad card"}')

    monkeypatch.setattr(feishu_im, "get_tenant_access_token", fake_token)
    monkeypatch.setattr(feishu_im, "request_with_retry", fake_request_with_retry)

    with pytest.raises(feishu_im.FeishuMessageError, match="status=400") as exc_info:
        await feishu_im.send_text("ou_missing", "hello")
    assert "bad card" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_message_returns_success_payload(monkeypatch) -> None:
    async def fake_token() -> str:
        return "tenant-token"

    async def fake_request_with_retry(*args, **kwargs) -> FakeResponse:
        return FakeResponse({"code": 0, "msg": "success", "data": {"message_id": "om_1"}})

    monkeypatch.setattr(feishu_im, "get_tenant_access_token", fake_token)
    monkeypatch.setattr(feishu_im, "request_with_retry", fake_request_with_retry)

    response = await feishu_im.send_text("ou_ok", "hello")

    assert response["data"]["message_id"] == "om_1"
