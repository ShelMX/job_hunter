from types import SimpleNamespace

import httpx
import respx

from job_hunter.infrastructure.config import LLMSettings
from job_hunter.infrastructure.http import HttpClient
from job_hunter.infrastructure.llm.ollama import LLMUnavailableError, OllamaLLM, model_size_gb

BASE = "http://localhost:11434"
TAGS = {"models": [{"name": "qwen3:4b"}, {"name": "gemma3:12b"}, {"name": "gemma2:2b"}]}


def _client(monkeypatch=None, free_gb: float = 40.0) -> tuple[OllamaLLM, HttpClient]:
    if monkeypatch:
        monkeypatch.setattr(
            "job_hunter.infrastructure.llm.ollama.psutil.virtual_memory",
            lambda: SimpleNamespace(available=free_gb * 2**30),
        )
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    return OllamaLLM(http, base_url=BASE, llm=LLMSettings()), http


def test_size_estimate() -> None:
    assert model_size_gb("qwen3:4b") == 4.0
    assert model_size_gb("gemma3:12b") == 12.0
    assert model_size_gb("no-size-tag") == 5.0


def test_pick_model_by_tier_and_ram(monkeypatch) -> None:
    client, http = _client(monkeypatch, free_gb=40.0)
    try:
        with respx.mock:
            respx.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
            assert client.pick_model("extract") == "qwen3:4b"
            assert client.pick_model("score") == "gemma3:12b"
    finally:
        http.close()


def test_pick_model_skips_non_chat_models(monkeypatch) -> None:
    """Модели с «экзотическими» capabilities (decision и т.п.) пропускаются для chat-ролей."""
    settings = LLMSettings()
    settings.models["extract"] = ["tev1:4b", "qwen3:4b"]
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    import job_hunter.infrastructure.llm.ollama as mod

    monkeypatch.setattr(mod.psutil, "virtual_memory", lambda: SimpleNamespace(available=40 * 2**30))
    client = OllamaLLM(http, base_url=BASE, llm=settings)
    try:
        with respx.mock:
            respx.get(f"{BASE}/api/tags").mock(
                return_value=httpx.Response(200, json={"models": [{"name": "tev1:4b"}, {"name": "qwen3:4b"}]})
            )
            respx.post(f"{BASE}/api/show").mock(
                side_effect=lambda req: httpx.Response(
                    200,
                    json={"capabilities": ["decision"]} if b"tev1" in req.content else {},
                )
            )
            assert client.pick_model("extract") == "qwen3:4b"
    finally:
        http.close()


def test_pick_model_degrades_when_ram_low(monkeypatch) -> None:
    client, http = _client(monkeypatch, free_gb=5.0)
    try:
        with respx.mock:
            respx.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
            assert client.pick_model("extract") is None  # даже 2b не влезает под политику
            assert client.pick_model("score") is None
    finally:
        http.close()


def test_decide_uses_systemone_for_decision_models(monkeypatch) -> None:
    """tev1 (capability=decision) → POST /v1/systemone, noul-вероятность → индекс опции."""
    settings = LLMSettings()
    settings.models["triage"] = ["tev1:4b", "qwen3:4b"]
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    import job_hunter.infrastructure.llm.ollama as mod

    monkeypatch.setattr(mod.psutil, "virtual_memory", lambda: SimpleNamespace(available=40 * 2**30))
    client = OllamaLLM(http, base_url=BASE, llm=settings)
    try:
        with respx.mock:
            respx.get(f"{BASE}/api/tags").mock(
                return_value=httpx.Response(200, json={"models": [{"name": "tev1:4b"}, {"name": "qwen3:4b"}]})
            )
            respx.post(f"{BASE}/api/show").mock(
                side_effect=lambda req: httpx.Response(
                    200, json={"capabilities": ["decision"]} if b"tev1" in req.content else {}
                )
            )
            systemone = respx.post(f"{BASE}/v1/systemone").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "answers": {
                            "fit": {"type": "noul", "noul": 0.87, "probabilities": {}, "confidence": 0.9}
                        }
                    },
                )
            )
            index = client.decide(role="triage", task="…вакансия/профиль…", options=["подходит", "нет"])

        assert index == 0  # p=0.87 ≥ 0.5 → опция 0
        import json

        body = json.loads(systemone.calls.last.request.content)
        assert body["model"] == "tev1:4b"
        assert body["keep_alive"] == "60s"
        assert body["questions"]["fit"]["type"] == "noul"
    finally:
        http.close()


def test_complete_sends_keep_alive_and_json(monkeypatch) -> None:
    client, http = _client(monkeypatch, free_gb=40.0)
    try:
        with respx.mock:
            respx.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
            chat = respx.post(f"{BASE}/api/chat").mock(
                return_value=httpx.Response(200, json={"message": {"content": '{"skills": []}'}})
            )
            out = client.complete(role="extract", system="sys", user="user")

        assert out == '{"skills": []}'
        payload = chat.calls.last.request
        import json

        body = json.loads(payload.content)
        assert body["format"] == "json"
        assert body["keep_alive"] == "60s"
        assert body["model"] == "qwen3:4b"
        assert body["options"]["temperature"] == 0.1
    finally:
        http.close()


def test_unload_only_used_models(monkeypatch) -> None:
    """Чужие загруженные модели (gemma4:12b пользователя) не выгружаем никогда."""
    client, http = _client(monkeypatch, free_gb=40.0)
    try:
        with respx.mock:
            respx.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
            respx.post(f"{BASE}/api/chat").mock(
                return_value=httpx.Response(200, json={"message": {"content": "{}"}})
            )
            respx.get(f"{BASE}/api/ps").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "models": [
                            {"name": "gemma4:12b", "size_vram": 10 * 2**30},
                            {"name": "qwen3:4b", "size_vram": 3 * 2**30},
                        ]
                    },
                )
            )
            generate = respx.post(f"{BASE}/api/generate").mock(
                return_value=httpx.Response(200, json={"done": True})
            )
            client.complete(role="extract", system="s", user="u")
            client.unload_used()

            assert generate.call_count == 1
            import json

            assert json.loads(generate.calls.last.request.content)["keep_alive"] == 0
            assert "gemma4:12b" not in json.loads(generate.calls.last.request.content)["model"]
            loaded = client.loaded_models_gb()
            assert loaded["gemma4:12b"] == 10.0
    finally:
        http.close()


def test_complete_raises_without_suitable_model(monkeypatch) -> None:
    client, http = _client(monkeypatch, free_gb=5.0)
    try:
        with respx.mock:
            respx.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
            try:
                client.complete(role="score", system="s", user="u")
                raise AssertionError("ожидали LLMUnavailableError")
            except LLMUnavailableError:
                pass
    finally:
        http.close()
