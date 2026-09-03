from types import SimpleNamespace

from app.infrastructure.external.llm import chat_model


def test_generic_openai_compatible_model_does_not_send_deepseek_extensions(monkeypatch):
    captured = {}

    def fake_init_chat_model(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(chat_model, "init_chat_model", fake_init_chat_model)
    settings = SimpleNamespace(
        model_provider="openai",
        model_name="deepseek-v4-flash",
        temperature=0,
        max_tokens=1000,
        llm_client_max_retries=0,
        api_base="https://example.invalid/v1",
        api_key="test-key",
        extra_headers=None,
    )

    chat_model.create_chat_model(settings)

    assert captured["model_provider"] == "openai"
    assert "extra_body" not in captured
