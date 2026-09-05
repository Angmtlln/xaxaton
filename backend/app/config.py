"""Конфигурация сервиса. Всё читается из окружения или .env."""
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Модель Master по умолчанию у OpenAI-совместимых провайдеров.
DEFAULT_GLM_MODEL = "z-ai/glm-5.3-flash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Контрагент-агент. PoC"
    app_version: str = "0.2.0"

    # --- Postgres ---
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/contractors",
        description="DSN подключения к PostgreSQL",
    )
    db_pool_min: int = 1
    db_pool_max: int = 8

    # --- Master Agent ---
    # Независимо от Groq-конфигурации доменных агентов ниже.
    master_provider: Literal["openrouter", "polza", "groq"] = "openrouter"
    master_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # OpenRouter показывает эти значения в статистике аккаунта; на вызов не влияют.
    openrouter_app_url: Optional[str] = None
    # Только латиница: HTTP-заголовок не принимает кириллицу.
    openrouter_app_title: str = "Counterparty Agent"
    # GLM тратит выходные токены на скрытые рассуждения и при коротком лимите
    # возвращает пустой content. Отключить их провайдер не даёт, но "low"
    # обнуляет рассуждения там, где нужен структурный ответ.
    openrouter_reasoning_effort: str = "low"
    polza_api_key: Optional[str] = None
    polza_base_url: str = "https://polza.ai/api/v1"

    # --- Groq ---
    groq_api_key: Optional[str] = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Модель блочных агентов по умолчанию.
    groq_block_model: str = "openai/gpt-oss-20b"
    # Модель summary: вызов один, собирает 4 блока в вывод на экран.
    groq_summary_model: str = "openai/gpt-oss-120b"
    # Legacy fallback для MASTER_PROVIDER=groq без явного MASTER_MODEL.
    groq_master_model: str = "openai/gpt-oss-120b"
    # Лимит бесплатного тарифа Groq (TPM) считается ОТДЕЛЬНО ПО КАЖДОЙ
    # МОДЕЛИ, а четыре агента идут параллельно и вместе весят около 8 тыс.
    # токенов. Поэтому блоки разведены по разным моделям: так проход
    # укладывается в лимит без ожидания. Формат: block=model через запятую.
    # gpt-oss-20b здесь не занят: это модель Master, и общий лимит с блоками
    # оставлял Master без токенов сразу после полной проверки.
    groq_block_model_map: str = (
        "identity=qwen/qwen3.8-27b,"
        "reliability=openai/gpt-oss-120b,"
        "finance=qwen/qwen3.8-27b,"
        "experience=qwen/qwen3.6-27b"
    )
    groq_timeout_s: float = 60.0
    groq_max_retries: int = 2
    block_temperature: float = 0.1
    summary_temperature: float = 0.2
    max_output_tokens: int = 2000
    # Лимит ответа router-модели; отчёт из её текста не строится.
    agent_router_max_tokens: int = 256
    # Запаса хватает на обход цепочки моделей и паузу, которую назвал Groq.
    agent_model_timeout_s: float = 45.0
    agent_tool_timeout_s: float = 150.0
    agent_run_timeout_s: float = 175.0
    agent_tool_result_max_chars: int = 120_000
    # gpt-oss тратит часть ответа на рассуждения. На "low" ответ короче в
    # четыре раза, влезает в лимит токенов и не обрывается на середине JSON.
    groq_reasoning_effort: str = "low"
    # Если модель упёрлась в лимит токенов, вызов уходит на следующую из
    # списка, а не ждёт минуту. Порядок — от лёгкой к тяжёлой.
    groq_fallback_models: str = "openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-120b"
    # Master упирается в тот же лимит TPM, что и доменные агенты: после полной
    # проверки его основная модель занята. Тот же вызов уходит на следующую
    # модель списка, иначе ответ Master вырождается в детерминированную заглушку.
    # Список моделей Groq, поэтому применяется только к MASTER_PROVIDER=groq.
    groq_master_fallback_models: str = "qwen/qwen3.8-27b,openai/gpt-oss-20b"

    # Каталог интерфейса. По умолчанию — frontend/ рядом с backend/.
    frontend_dir: Optional[str] = None

    # Работа без ключа: детерминированный псевдо-LLM для демо и тестов.
    llm_mock: bool = False

    # Версия детерминированного калькулятора фактов (S2). Меняем при
    # изменении логики расчёта, чтобы кэш audit.snapshot_facts не протух.
    calculator_version: str = "facts-1.0.0"

    def master_model_name(self) -> str:
        if self.master_model:
            return self.master_model
        if self.master_provider in {"openrouter", "polza"}:
            return DEFAULT_GLM_MODEL
        return self.groq_master_model

    def answer_max_tokens(self) -> int:
        """Бюджет ответа Master. У Groq он упирается в лимит выходных токенов."""
        return 600 if self.master_provider == "groq" else 1100

    def verifier_max_tokens(self) -> int:
        """Вердикт заземления — короткий JSON, но reasoning-модели нужен запас."""
        return 200 if self.master_provider == "groq" else 500

    def master_api_key(self) -> Optional[str]:
        """Ключ выбранного провайдера Master; None означает offline-режим."""
        return {
            "openrouter": self.openrouter_api_key,
            "polza": self.polza_api_key,
            "groq": self.groq_api_key,
        }[self.master_provider]

    def master_model_chain(self) -> list:
        """Модель Master и её запасные по порядку, без повторов."""
        chain = [self.master_model_name()]
        if self.master_provider != "groq":
            return chain
        for name in self.groq_master_fallback_models.split(","):
            name = name.strip()
            if name and name not in chain:
                chain.append(name)
        return chain

    def model_for_block(self, block: str) -> str:
        """Модель конкретного блочного агента."""
        for pair in self.groq_block_model_map.split(","):
            if "=" not in pair:
                continue
            key, _, model = pair.partition("=")
            if key.strip() == block and model.strip():
                return model.strip()
        return self.groq_block_model

    def fallback_models(self) -> list:
        return [m.strip() for m in self.groq_fallback_models.split(",") if m.strip()]

    def block_models(self) -> dict:
        from app.domain.facts import BLOCK_KEYS
        return {block: self.model_for_block(block) for block in BLOCK_KEYS}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
