"""Request-local OpenRouter accounting, accumulated by the conversation lease."""
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field


class ConversationUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    calls: int = Field(default=0, ge=0)
    incomplete: bool = False

    def record(self, usage: dict | None) -> None:
        self.calls += 1
        usage = usage or {}
        for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            value = usage.get(source)
            if type(value) is int and value >= 0:
                setattr(self, target, getattr(self, target) + value)
            else:
                self.incomplete = True
        try:
            cost = Decimal(str(usage.get("cost")))
            if not cost.is_finite() or cost < 0:
                raise ValueError("Invalid provider cost")
            self.cost_usd = float(Decimal(str(self.cost_usd)) + cost)
        except (InvalidOperation, ValueError):
            self.incomplete = True


active_usage: ContextVar[ConversationUsage | None] = ContextVar("openrouter_usage", default=None)
