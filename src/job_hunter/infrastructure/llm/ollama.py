"""Адаптер Ollama (нативное /api/* — OpenAI-совместимый /v1 игнорирует keep_alive).

Ресурсная дисциплина (PLAN §7.1):
- выбор модели по тиру роли с проверкой свободной RAM (psutil);
- keep_alive на период батча, выгрузка задействованных моделей после (unload_used);
- чужие модели (загруженные вне нашей сессии) не трогаем никогда.
"""

import logging
import re
from collections.abc import Sequence
from contextlib import suppress

import psutil

from job_hunter.domain.ports import LLMUnavailableError
from job_hunter.infrastructure.config import LLMSettings
from job_hunter.infrastructure.http import HttpClient

logger = logging.getLogger(__name__)

__all__ = ["OllamaLLM", "LLMUnavailableError", "model_size_gb", "build_decision_prompt", "parse_letter"]


_CHAT_CAPABLE = {"completion", "chat", "insert", "tools", "thinking", "vision"}


def model_size_gb(name: str) -> float:
    """Грубая оценка размера по имени (qwen3:4b → 4)."""
    match = re.search(r"(\d+(?:\.\d+)?)b", name.lower())
    return float(match.group(1)) if match else 5.0


def build_decision_prompt(task: str, options: Sequence[str]) -> str:
    labeled = "\n".join(f"{chr(ord('A') + i)}) {opt}" for i, opt in enumerate(options))
    return f"Задача:\n{task}\n\nВарианты:\n{labeled}\n\nОтвет — только буква варианта."


_CYR_TO_LAT = {"А": "A", "Б": "B", "В": "C", "Г": "D"}  # модели иногда отвечают кириллицей


def parse_letter(answer: str, n_options: int) -> int | None:
    """«B»/«Б» → 1. На входе возможна обёртка вида «Ответ: Б»."""
    text = answer.strip().upper()
    for cyr, lat in _CYR_TO_LAT.items():
        text = text.replace(cyr, lat)
    match = re.search(r"\b([A-Z])\b", text)
    if not match:
        return None
    index = ord(match.group(1)) - ord("A")
    return index if 0 <= index < n_options else None


class OllamaLLM:
    def __init__(self, http: HttpClient, *, base_url: str, llm: LLMSettings) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._llm = llm
        self._used_models: set[str] = set()
        self._caps_cache: dict[str, list[str]] = {}

    def _capabilities(self, name: str) -> list[str]:
        """Capabilities из /api/show; неизвестно → считаем chat-совместимой."""
        if name not in self._caps_cache:
            try:
                data = self._http.post_json(f"{self._base_url}/api/show", json={"name": name}, timeout=10)
                self._caps_cache[name] = [str(c) for c in (data.get("capabilities") or [])]
            except Exception:
                self._caps_cache[name] = []
        return self._caps_cache[name]

    def _is_chat_capable(self, name: str) -> bool:
        caps = set(self._capabilities(name))
        return not caps or bool(caps & _CHAT_CAPABLE)

    def provider_name(self) -> str:
        return "ollama"

    def is_available(self) -> bool:
        try:
            self._http.get_json(f"{self._base_url}/api/tags", timeout=4)
            return True
        except Exception:
            logger.debug("Ollama недоступна на %s", self._base_url)
            return False

    def _available_models(self) -> set[str]:
        data = self._http.get_json(f"{self._base_url}/api/tags", timeout=10)
        return {str(m.get("name")) for m in data.get("models", [])}

    def pick_model(self, role: str) -> str | None:
        """Первый кандидат тира, влезающий в свободную RAM (need = size*1.3 + 2 ГБ ОС-запаса)."""
        free_gb = psutil.virtual_memory().available / 2**30
        try:
            available = self._available_models()
        except Exception:
            logger.warning("Не смог получить список моделей Ollama")
            return None
        candidates = self._llm.models.get(role, [])
        # max_loaded_models=1 (§7.1): если в сессии уже загружена модель из тира — берём её
        for used in self._used_models:
            if used in candidates:
                return used
        for candidate in candidates:
            if candidate not in available:
                continue
            if not self._is_chat_capable(candidate):
                logger.info(
                    "Модель %s пропущена: capabilities=%s (нет chat/generate)",
                    candidate,
                    self._capabilities(candidate),
                )
                continue
            need = model_size_gb(candidate) * 1.3 + 2.0
            if free_gb >= max(need, self._llm.resource_policy.min_free_ram_gb):
                return candidate
            logger.info("Модель %s пропущена: нужно ~%.1f ГБ, свободно %.1f ГБ", candidate, need, free_gb)
        return None

    def complete(self, *, role: str, system: str, user: str) -> str:
        model = self.pick_model(role)
        if model is None:
            raise LLMUnavailableError(f"нет подходящей модели для роли {role!r} (RAM/наличие)")
        self._used_models.add(model)
        return self._chat_with_model(model, system=system, user=user, expect_json=True)

    def decide(self, *, role: str, task: str, options: Sequence[str]) -> int | None:
        """Decision-задача. Decision-модели (tev1, clef-flash) → /v1/systemone (Jev API);
        обычные chat-модели → буква варианта."""
        available: set[str] = set()
        with suppress(Exception):
            available = self._available_models()
        for candidate in self._llm.models.get(role, []):
            if candidate not in available or "decision" not in self._capabilities(candidate):
                continue
            if candidate in self._used_models:
                # уже резидент в этой сессии — повторный RAM-гейт бессмысленен и ломал батч
                return self._systemone_decide(candidate, task, options)
            # та же политика экономии RAM, что и у chat-моделей — но только при первой загрузке
            free_gb = psutil.virtual_memory().available / 2**30
            need = model_size_gb(candidate) * 1.3 + 2.0
            if free_gb >= max(need, self._llm.resource_policy.min_free_ram_gb):
                return self._systemone_decide(candidate, task, options)
            logger.info(
                "Decision-модель %s пропущена: нужно ~%.1f ГБ, свободно %.1f ГБ", candidate, need, free_gb
            )

        # fallback: chat-модель отвечает буквой
        model = self.pick_model(role)
        if model is None:
            raise LLMUnavailableError(f"нет подходящей модели для роли {role!r} (RAM/наличие)")
        self._used_models.add(model)
        answer = self._chat_with_model(
            model,
            system="Отвечай только одной буквой выбранного варианта, без пояснений.",
            user=build_decision_prompt(task, options),
            expect_json=False,
        )
        return parse_letter(answer, len(options))

    def _systemone_decide(self, model: str, task: str, options: Sequence[str]) -> int | None:
        """Jev/System One API: noul-вариант «option 0 — верно?» с вероятностью."""
        self._used_models.add(model)
        data = self._http.post_json(
            f"{self._base_url}/v1/systemone",
            json={
                "model": model,
                "state": task,
                "questions": {
                    "fit": {
                        "type": "noul",
                        "instructions": f"Верно ли для описанного state: «{options[0]}»?",
                        "criteria": {
                            "true": options[0],
                            "false": options[1] if len(options) > 1 else "не подходит",
                        },
                    }
                },
                "keep_alive": self._llm.resource_policy.keep_alive,
            },
            timeout=120,
        )
        answers = data.get("answers") or {}
        fit = answers.get("fit") or {}
        if "noul" not in fit:
            return None
        probability = float(fit["noul"])
        logger.info("systemone %s: p(подходит)=%.2f", model, probability)
        return 0 if probability >= 0.5 else 1

    def _chat_with_model(self, model: str, *, system: str, user: str, expect_json: bool) -> str:
        data = self._http.post_json(
            f"{self._base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                **({"format": "json"} if expect_json else {}),
                "stream": False,
                "keep_alive": self._llm.resource_policy.keep_alive,
                # num_ctx ограничен осознанно: дефолтные 32-256k съедают RAM в разы больше «веса» модели
                "options": {"temperature": 0.1, "num_ctx": 8192},
            },
            timeout=180,
        )
        return str((data.get("message") or {}).get("content") or "")

    def unload_used(self) -> None:
        for model in sorted(self._used_models):
            try:
                self._http.post_json(
                    f"{self._base_url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30
                )
                logger.info("Ollama: модель %s выгружена", model)
            except Exception:
                logger.warning("Ollama: не смог выгрузить %s", model)
        self._used_models.clear()

    def loaded_models_gb(self) -> dict[str, float]:
        """Телеметрия: кто сейчас в VRAM (по /api/ps), имя → ГБ."""
        try:
            data = self._http.get_json(f"{self._base_url}/api/ps", timeout=5)
        except Exception:
            return {}
        return {
            str(m.get("name")): round(int(m.get("size_vram") or 0) / 2**30, 1) for m in data.get("models", [])
        }
