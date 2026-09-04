"""Конфигурация сервиса. Всё читается из окружения или .env."""
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Groq ---
    groq_api_key: Optional[str] = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Модель блочных агентов по умолчанию.
    groq_block_model: str = "openai/gpt-oss-20b"
    # Модель summary: вызов один, собирает 4 блока в вывод на экран.
    groq_summary_model: str = "openai/gpt-oss-120b"
    # Master Agent первого vertical slice только маршрутизирует один JSON-action.
    groq_master_model: str = "openai/gpt-oss-20b"
    # Лимит бесплатного тарифа Groq (TPM) считается ОТДЕЛЬНО ПО КАЖДОЙ
    # МОДЕЛИ, а четыре агента идут параллельно и вместе весят около 8 тыс.
    # токенов. Поэтому блоки разведены по разным моделям: так проход
    # укладывается в лимит без ожидания. Формат: block=model через запятую.
    groq_block_model_map: str = (
        "identity=openai/gpt-oss-20b,"
        "reliability=openai/gpt-oss-120b,"
        "finance=qwen/qwen3.8-27b,"
        "experience=openai/gpt-oss-20b"
    )
    groq_timeout_s: float = 60.0
    groq_max_retries: int = 2
    block_temperature: float = 0.1
    summary_temperature: float = 0.2
    max_output_tokens: int = 2000
    agent_router_max_tokens: int = 256
    agent_model_timeout_s: float = 20.0
    agent_tool_timeout_s: float = 150.0
    agent_run_timeout_s: float = 175.0
    agent_tool_result_max_chars: int = 120_000
    # gpt-oss тратит часть ответа на рассуждения. На "low" ответ короче в
    # четыре раза, влезает в лимит токенов и не обрывается на середине JSON.
    groq_reasoning_effort: str = "low"
    # Если модель упёрлась в лимит токенов, вызов уходит на следующую из
    # списка, а не ждёт минуту. Порядок — от лёгкой к тяжёлой.
    groq_fallback_models: str = "openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-120b"

    # Работа без ключа: детерминированный псевдо-LLM для демо и тестов.
    llm_mock: bool = False

    # Версия детерминированного калькулятора фактов (S2). Меняем при
    # изменении логики расчёта, чтобы кэш audit.snapshot_facts не протух.
    calculator_version: str = "facts-1.0.0"


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
