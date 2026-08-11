import pytest

from app.adapters import feishu_chat


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
async def test_create_chat_sends_open_id_user_list_and_bot_list(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_token() -> str:
        return "tenant-token"

    async def fake_request_with_retry(client, method, path, **kwargs) -> FakeResponse:
        calls.append({"method": method, "path": path, **kwargs})
        return FakeResponse({"code": 0, "data": {"chat_id": "oc_1"}})

    monkeypatch.setattr(feishu_chat, "get_tenant_access_token", fake_token)
    monkeypatch.setattr(feishu_chat, "request_with_retry", fake_request_with_retry)

    response = await feishu_chat.create_chat(
        name="张三 入职协同群",
        owner_open_id="ou_hr",
        user_open_ids=["ou_candidate", "ou_hr", "ou_hr"],
        bot_app_ids=["cli_bot"],
    )

    assert feishu_chat.extract_chat_id(response) == "oc_1"
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/open-apis/im/v1/chats"
    assert calls[0]["headers"]["Authorization"] == "Bearer tenant-token"
    assert calls[0]["params"] == {"user_id_type": "open_id"}
    assert calls[0]["json"]["owner_id"] == "ou_hr"
    assert calls[0]["json"]["user_id_list"] == ["ou_candidate", "ou_hr"]
    assert calls[0]["json"]["bot_id_list"] == ["cli_bot"]


@pytest.mark.asyncio
async def test_create_chat_redacts_feishu_http_error_body(monkeypatch) -> None:
    async def fake_token() -> str:
        return "tenant-token"

    async def fake_request_with_retry(client, method, path, **kwargs) -> FakeResponse:
        return FakeResponse({}, status_code=400, text='{"code":230099,"msg":"bad chat"}')

    monkeypatch.setattr(feishu_chat, "get_tenant_access_token", fake_token)
    monkeypatch.setattr(feishu_chat, "request_with_retry", fake_request_with_retry)

    with pytest.raises(feishu_chat.FeishuChatError, match="status=400") as exc_info:
        await feishu_chat.create_chat(name="张三 入职协同群", user_open_ids=["ou_candidate"])
    assert "bad chat" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_add_chat_members_splits_users_and_bots(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_token() -> str:
        return "tenant-token"

    async def fake_request_with_retry(client, method, path, **kwargs) -> FakeResponse:
        calls.append({"method": method, "path": path, **kwargs})
        return FakeResponse({"code": 0, "data": {"invalid_id_list": []}})

    monkeypatch.setattr(feishu_chat, "get_tenant_access_token", fake_token)
    monkeypatch.setattr(feishu_chat, "request_with_retry", fake_request_with_retry)

    result = await feishu_chat.add_chat_members("oc_1", user_open_ids=["ou_a"], bot_app_ids=["cli_bot"])

    assert result["users"]["code"] == 0
    assert result["bots"]["code"] == 0
    assert calls[0]["path"] == "/open-apis/im/v1/chats/oc_1/members"
    assert calls[0]["params"] == {"member_id_type": "open_id", "succeed_type": "1"}
    assert calls[0]["json"] == {"id_list": ["ou_a"]}
    assert calls[1]["params"] == {"member_id_type": "app_id", "succeed_type": "1"}
    assert calls[1]["json"] == {"id_list": ["cli_bot"]}
