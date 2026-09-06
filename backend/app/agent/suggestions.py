"""Next questions: Master proposals, exact value checks and offline defaults."""
import re

from .grounding import backend_owned_violations
from .models import SuggestedAction


def next_actions(answer, context, *, contextual=False):
    companies = [context.get("company") or {}, *context.get("companies", []), *(context.get("connections") or {}).get("nodes", [])]
    known = {str(item.get("inn")) for item in companies if item.get("inn")}
    if answer is not None:
        actions, seen = [], set()
        for action in answer.suggested_actions:
            text = action.label + " " + action.prompt
            # Identifiers in prompts must be verified even without an ИНН prefix.
            identifiers = set(re.findall(r"(?<!\d)\d{10,15}(?!\d)", text))
            if backend_owned_violations(text, context) or identifiers - known:
                continue
            # Comparison is a routed capability, not a semantic prose check.
            # Complete a draft from backend-owned identity; the model may use
            # only the company name, which the comparison API cannot resolve.
            if re.search(r"\b(?:сравн\w*|compare\w*)\b", action.prompt, re.I):
                inn = (context.get("company") or {}).get("inn")
                prompt = action.prompt
                if inn and str(inn) not in prompt:
                    prompt = f"ИНН текущего контрагента: {inn}. " + prompt
                mode = "compose" if len(identifiers) < 2 else action.mode
                action = SuggestedAction(label=action.label, prompt=prompt[:300], mode=mode)
            key = action.prompt.strip().casefold()
            if key and key not in seen:
                actions.append(action)
                seen.add(key)
        if actions or (not answer.suggested_actions and "suggested_actions" in answer.model_fields_set):
            return actions

    def action(label, prompt=None, mode="submit"):
        return SuggestedAction(label=label, prompt=prompt or label, mode=mode)

    domain = context.get("domain")
    if domain == "intro":
        return [action("Проверить контрагента", "Проверь контрагента ", "compose"),
                action("Сравнить контрагентов", "Сравни контрагентов: ", "compose"),
                action("Что умеет аналитик?", "Чем ты можешь помочь при выборе контрагента?")]
    if domain == "comparison":
        return ([action("Что уточнить перед выбором?"), action("Как условия сделки меняют выбор?")]
                if contextual else [action("Кого выбрать и почему?"), action("Что уточнить у каждого контрагента?")])
    inn = (context.get("company") or {}).get("inn")
    compare = action("Сравнить с другими", f"Сравни контрагентов: {inn} и " if inn else "Сравни контрагентов: ", "compose")
    if contextual:
        return [action("Составить вопросы контрагенту", "Какие вопросы задать контрагенту по этому выводу?"), compare]
    if domain == "full_check":
        return [compare, action("Что уточнить у контрагента?"), action("Разобрать финансы", "А что у них с финансами?")]
    if domain == "finance":
        return [action("Разобрать динамику", "Что означает эта финансовая динамика?"), action("Что уточнить по финансам?"), compare]
    return [action("Что уточнить по судебным событиям?"), action("Объяснить проще", "Объясни проще"), compare]
