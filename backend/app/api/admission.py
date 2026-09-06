"""Small process-local admission gate for anonymous demo API mutations."""
import asyncio
import time
from collections import deque

from starlette.responses import JSONResponse


class ApiAdmission:
    def __init__(self, settings):
        self.settings = settings
        self.active = 0
        # Bounded by the global per-minute limit, including peer cardinality.
        self.recent = deque()

    def enter(self, peer):
        now = time.monotonic()
        while self.recent and self.recent[0][0] <= now - 60:
            self.recent.popleft()
        same_peer = [stamp for stamp, ip in self.recent if ip == peer]
        if len(self.recent) >= self.settings.api_requests_per_minute:
            return 429, max(1, int(self.recent[0][0] + 60 - now) + 1)
        if len(same_peer) >= self.settings.api_requests_per_ip_per_minute:
            return 429, max(1, int(same_peer[0] + 60 - now) + 1)
        if self.active >= self.settings.api_max_concurrent:
            return 503, 5
        # No await between capacity check and reservation on the ASGI loop.
        self.recent.append((now, peer))
        self.active += 1
        return None


class ApiAdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or not scope['path'].startswith('/api/')
                or scope['method'] not in {'POST', 'PUT', 'PATCH', 'DELETE'}):
            return await self.app(scope, receive, send)
        gate = scope['app'].state.api_admission
        peer = (scope.get('client') or ('unknown',))[0]
        # Use the ASGI peer; never parse attacker-provided X-Forwarded-For here.
        denial = gate.enter(peer)
        if denial:
            status, retry_after = denial
            return await JSONResponse(
                {'detail': 'Слишком много запросов. Повторите немного позже.'},
                status_code=status, headers={'Retry-After': str(retry_after)},
            )(scope, receive, send)
        try:
            maximum = gate.settings.api_max_body_bytes
            headers = dict(scope['headers'])
            try:
                length = int(headers.get(b'content-length', b'0'))
                if length < 0:
                    raise ValueError()
            except ValueError:
                return await JSONResponse({'detail': 'Некорректная длина запроса.'}, 400)(scope, receive, send)
            if length > maximum:
                return await JSONResponse({'detail': 'Запрос слишком большой.'}, 413)(scope, receive, send)
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        event = await receive()
                        if event['type'] == 'http.disconnect':
                            return
                        chunk = event.get('body', b'')
                        if len(body) + len(chunk) > maximum:
                            return await JSONResponse({'detail': 'Запрос слишком большой.'}, 413)(scope, receive, send)
                        body.extend(chunk)
                        if not event.get('more_body', False):
                            break
            except TimeoutError:
                return await JSONResponse({'detail': 'Время загрузки запроса истекло.'}, 408)(scope, receive, send)
            pending = True

            async def replay():
                nonlocal pending
                if pending:
                    pending = False
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()

            # Hold the slot through the entire stream, including cancellation.
            await self.app(scope, replay, send)
        finally:
            gate.active -= 1
