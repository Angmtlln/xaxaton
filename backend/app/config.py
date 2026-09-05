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

    # --- Master Agent ---
    # Независимо от Groq-конфигурации доменных агентов ниже.
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    master_model: str = "z-ai/glm-5.3-flash"
    # OpenRouter показывает эти значения в статистике аккаунта; на вызов не влияют.
    openrouter_app_url: Optional[str] = None
    # Только латиница: HTTP-заголовок не принимает кириллицу.
    openrouter_app_title: str = "Counterparty Agent"
    # GLM тратит выходные токены на скрытые рассуждения и при коротком лимите
    # возвращает пустой content. Отключить их провайдер не даёт, но "low"
    # обнуляет рассуждения там, где нужен структурный ответ.
    openrouter_reasoning_effort: str = "low"
    # Короткий JSON-verdict на GLM должен оставлять output budget под сам ответ.
    openrouter_verifier_reasoning_effort: str = "low"

    # --- Groq ---
    groq_api_key: Optional[str] = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Модель блочных агентов по умолчанию.
    groq_block_model: str = "openai/gpt-oss-20b"
    # Модель summary: вызов один, собирает 4 блока в вывод на экран.
    groq_summary_model: str = "openai/gpt-oss-120b"
    # Лимит бесплатного тарифа Groq (TPM) считается ОТДЕЛЬНО ПО КАЖДОЙ
    # МОДЕЛИ, а четыре агента идут параллельно и вместе весят около 8 тыс.
    # токенов. Поэтому блоки разведены по разным моделям: так проход
    # укладывается в лимит без ожидания. Формат: block=model через запятую.
    # Блоки разведены по моделям, чтобы параллельный проход не делил один TPM.
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
    # Routing возвращает только tool call, поэтому ему достаточно 512.
    agent_router_max_tokens: int = 512
    # GLM-5.3-Flash считает reasoning частью output budget. Каждый
    # этап имеет отдельный конечный лимит: более длинный synthesis/repair
    # и короткий, но с запасом на reasoning, JSON-verifier.
    agent_answer_max_tokens: int = 4096
    agent_verifier_max_tokens: int = 4096
    agent_repair_max_tokens: int = 4096
    # Запаса хватает на полный tool/answer/grounding проход Master.
    agent_model_timeout_s: float = 90.0
    agent_verifier_timeout_s: float = 75.0
    agent_tool_timeout_s: float = 150.0
    agent_run_timeout_s: float = 300.0
    agent_tool_result_max_chars: int = 120_000
    # gpt-oss тратит часть ответа на рассуждения. На "low" ответ короче в
    # четыре раза, влезает в лимит токенов и не обрывается на середине JSON.
    groq_reasoning_effort: str = "low"
    # Если модель упёрлась в лимит токенов, вызов уходит на следующую из
    # списка, а не ждёт минуту. Порядок — от лёгкой к тяжёлой.
    groq_fallback_models: str = "openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-120b"
    # Каталог интерфейса. По умолчанию — frontend/ рядом с backend/.
    frontend_dir: Optional[str] = None

    # Работа без ключа: детерминированный псевдо-LLM для демо и тестов.
    llm_mock: bool = False

    # Версия детерминированного калькулятора фактов (S2). Меняем при
    # изменении логики расчёта, чтобы кэш audit.snapshot_facts не протух.
    calculator_version: str = "facts-1.0.0"

    def answer_max_tokens(self) -> int:
        """Бюджет естественного ответа Master через OpenRouter."""
        return self.agent_answer_max_tokens

    def verifier_max_tokens(self) -> int:
        """Бюджет короткого JSON-вердикта grounding verifier."""
        return self.agent_verifier_max_tokens

    def repair_max_tokens(self) -> int:
        """Бюджет единственной repair-попытки Master."""
        return self.agent_repair_max_tokens

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
