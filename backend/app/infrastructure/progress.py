"""Request-local public stage events. No prompts, secrets or model reasoning."""
from contextvars import ContextVar
from typing import Callable, Optional

progress_sink: ContextVar[Optional[Callable[[dict], None]]] = ContextVar('progress_sink', default=None)
STAGES = {
    'accepted': ('Разбираюсь в вашем вопросе', 'Запрос принят, выбираю нужные данные.'),
    'snapshot': ('Открываю карточку компании', 'Читаю последний доступный снимок.'),
    'analysis': ('Изучаю данные компании', 'Сопоставляю финансы, правовые события и сведения о деятельности.'),
    'verification': ('Проверяю факты отчёта', 'Сверяю показатели и сигналы с исходными данными.'),
    'connections': ('Ищу пересечения в датасете', 'Сопоставляю учредителей, руководителей, связанные ИНН и контакты.'),
    'neighbours': ('Изучаю связанные компании', 'Собираю краткий срез сведений по найденным соседям.'),
    'finance': ('Разбираю финансовые данные', 'Читаю показатели и доступные периоды отчётности.'),
    'legal': ('Изучаю выбранный раздел карточки', 'Собираю проверенные сведения по вашему вопросу.'),
    'comparison': ('Сопоставляю контрагентов', 'Собираю сравнимые данные выбранных компаний.'),
    'context': ('Готовлю пояснение', 'Учитываю ваш вопрос и доступный контекст диалога.'),
    'synthesis': ('Формирую ответ', 'Выделяю значимые наблюдения и объясняю их.'),
    'graph': ('Строю граф связей', 'Использую уже проверенные узлы и основания связи.'),
}


def emit_progress(stage):
    sink = progress_sink.get()
    if sink is not None and stage in STAGES:
        title, detail = STAGES[stage]
        sink({'type': 'progress', 'stage': stage, 'title': title, 'detail': detail})
