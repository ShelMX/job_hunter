"""Адаптер LM Studio (fallback, OpenAI-совместимый /v1).

Выгрузка: у LM Studio включён auto-unload по бездействию (настройка пользователя 1 мин);
дополнительно — best-effort `lms unload` после батча (если CLI на PATH).
"""

import logging
import shutil
import subprocess
from collections.abc import Sequence

from job_hunter.infrastructure.config import LLMSettings
from job_hunter.infrastructure.http import HttpClient
from job_hunter.infrastructure.llm.ollama import (
    LLMUnavailableError,
    build_decision_prompt,
    parse_letter,
)

logger = logging.getLogger(__name__)


class LMStudioLLM:
    def __init__(self, http: HttpClient, *, base_url: str, llm: LLMSettings) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._llm = llm
        self._used_models: set[str] = set()

    def provider_name(self) -> str:
        return "lmstudio"

    def is_available(self) -> bool:
        try:
            data = self._http.get_json(f"{self._base_url}/models", timeout=4)
            return bool(data.get("data"))
        except Exception:
            logger.debug("LM Studio недоступна на %s", self._base_url)
            return False

    def _pick_model(self, role: str) -> str | None:
        """LM Studio виден список моделей с id вида 'qwen/qwen3.5-9b': мэтчим по подстроке имени тира."""
        data = self._http.get_json(f"{self._base_url}/models", timeout=10)
        ids = [str(m.get("id")) for m in data.get("data", [])]
        for candidate in self._llm.models.get(role, []):
            stem = candidate.split(":", 1)[0]  # qwen3:4b → qwen3
            for model_id in ids:
                if stem in model_id:
                    return model_id
        return ids[0] if ids else None

    def complete(self, *, role: str, system: str, user: str) -> str:
        model = self._pick_model(role)
        if model is None:
            raise LLMUnavailableError("в LM Studio нет загруженных моделей")
        self._used_models.add(model)
        data = self._http.post_json(
            f"{self._base_url}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=240,
        )
        choices = data.get("choices") or []
        return str((choices[0].get("message") or {}).get("content") or "") if choices else ""

    def decide(self, *, role: str, task: str, options: Sequence[str]) -> int | None:
        model = self._pick_model(role)
        if model is None:
            raise LLMUnavailableError("в LM Studio нет загруженных моделей")
        self._used_models.add(model)
        data = self._http.post_json(
            f"{self._base_url}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Отвечай только буквой выбранного варианта."},
                    {"role": "user", "content": build_decision_prompt(task, options)},
                ],
                "temperature": 0.0,
            },
            timeout=180,
        )
        choices = data.get("choices") or []
        answer = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
        return parse_letter(answer, len(options))

    def unload_used(self) -> None:
        """Auto-unload уже настроен в LM Studio; `lms unload` — best effort сверху."""
        if not self._llm.resource_policy.lmstudio_unload_via_cli or not shutil.which("lms"):
            return
        for model in sorted(self._used_models):
            try:
                subprocess.run(["lms", "unload", model], timeout=30, check=False, capture_output=True)
                logger.info("LM Studio: модель %s выгружена через lms", model)
            except Exception:
                logger.warning("LM Studio: не удалось выгрузить %s", model)
        self._used_models.clear()
