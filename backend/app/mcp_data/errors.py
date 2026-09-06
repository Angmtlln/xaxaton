"""Safe errors crossing the source boundary; never contain upstream payloads."""
class CompanySourceError(Exception):
    code = 'source_unavailable'
    http_status = 503
    retryable = True
    message = 'Источник данных контрагентов временно недоступен. Попробуйте ещё раз.'

    def __init__(self):
        super().__init__(self.message)


class SourceTimeout(CompanySourceError):
    code = 'timeout'
    message = 'Источник данных не ответил вовремя. Попробуйте ещё раз.'


class InvalidSourceResponse(CompanySourceError):
    code = 'internal_error'
    http_status = 502
    retryable = False
    message = 'Источник вернул некорректные данные. Проверку не удалось завершить.'


class SourceResultTooLarge(InvalidSourceResponse):
    code = 'result_too_large'
    message = 'Объём ответа источника превышает допустимый размер.'
