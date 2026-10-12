"""Фабрика LLM-провайдера: Ollama (primary) → LM Studio (fallback). Никогда оба сразу."""

import logging

from job_hunter.domain.ports import LLMClient
from job_hunter.infrastructure.config import Settings
from job_hunter.infrastructure.http import HttpClient
from job_hunter.infrastructure.llm.lmstudio import LMStudioLLM
from job_hunter.infrastructure.llm.ollama import OllamaLLM

logger = logging.getLogger(__name__)


def build_llm(settings: Settings, http: HttpClient) -> LLMClient | None:
    """Вернуть доступного провайдера или None (тогда система работает в rules-режиме)."""
    if not settings.llm.enabled:
        return None
    for client in (
        OllamaLLM(http, base_url=settings.llm.primary_base_url, llm=settings.llm),
        LMStudioLLM(http, base_url=settings.llm.fallback_base_url, llm=settings.llm),
    ):
        if client.is_available():
            logger.info("LLM-провайдер: %s", client.provider_name())
            return client
    logger.warning("LLM-провайдеры недоступны — работаем в rules-режиме")
    return None
