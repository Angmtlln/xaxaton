"""Limits apply before domain work and survive streaming/cancellation."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from app.api.admission import ApiAdmission, ApiAdmissionMiddleware
from app.config import Settings


def gate(**kwargs):
    return ApiAdmission(Settings(_env_file=None, **kwargs))


def scope(limits, headers=(), path='/api/v1/chat/messages'):
    return {'type': 'http', 'method': 'POST', 'path': path,
            'headers': list(headers), 'client': ('peer', 1234),
            'app': SimpleNamespace(state=SimpleNamespace(api_admission=limits))}


async def receive_empty():
    return {'type': 'http.request', 'body': b'{}', 'more_body': False}


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [[], [(b'content-length', b'2048')]])
async def test_oversized_body_never_reaches_domain(headers):
    limits = gate(api_max_body_bytes=1024)
    called, sent = [], []
    events = iter([{'type': 'http.request', 'body': b'x' * 800, 'more_body': True},
                   {'type': 'http.request', 'body': b'x' * 800, 'more_body': False}])
    async def receive():
        return next(events)
    async def downstream(*args):
        called.append(True)
    async def send(event):
        sent.append(event)
    await ApiAdmissionMiddleware(downstream)(scope(limits, headers), receive, send)
    assert sent[0]['status'] == 413
    assert not called and limits.active == 0


@pytest.mark.asyncio
async def test_shared_gate_holds_stream_slot_and_rejects_legacy_until_finished():
    limits = gate(api_max_concurrent=1)
    started, release = asyncio.Event(), asyncio.Event()
    calls, sent = [], []
    async def stream(scope, receive, send):
        calls.append(scope['path'])
        assert (await receive())['body'] == b'{}'
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        started.set()
        await release.wait()
        await send({'type': 'http.response.body', 'body': b'done'})
    async def send(event):
        sent.append(event)
    middleware = ApiAdmissionMiddleware(stream)
    task = asyncio.create_task(middleware(scope(limits, path='/api/v1/chat/messages/stream'), receive_empty, send))
    await started.wait()
    await middleware(scope(limits, path='/api/v1/checks'), receive_empty, send)
    assert sent[-2]['status'] == 503
    assert calls == ['/api/v1/chat/messages/stream'] and limits.active == 1
    release.set()
    await task
    assert limits.active == 0
    await middleware(scope(limits, path='/api/v1/checks'), receive_empty, send)
    assert calls[-1] == '/api/v1/checks'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_failure_and_cancellation_release_slot(failure):
    limits = gate(api_max_concurrent=1)
    async def fail(*args):
        raise failure()
    with pytest.raises(failure):
        await ApiAdmissionMiddleware(fail)(scope(limits), receive_empty, None)
    assert limits.active == 0


def test_ip_and_global_windows_are_bounded_and_expire(monkeypatch):
    now = [100.0]
    monkeypatch.setattr('app.api.admission.time.monotonic', lambda: now[0])
    limits = gate(api_requests_per_minute=2, api_requests_per_ip_per_minute=1)
    assert limits.enter('a') is None
    limits.active -= 1
    assert limits.enter('a') == (429, 61)
    assert limits.enter('b') is None
    limits.active -= 1
    assert limits.enter('c') == (429, 61)
    assert len(limits.recent) == 2
    now[0] += 61
    assert limits.enter('a') is None
    assert len(limits.recent) == 1


@pytest.mark.asyncio
async def test_forwarded_header_cannot_reset_peer_budget():
    limits = gate(api_requests_per_ip_per_minute=1)
    calls, sent = [], []
    async def downstream(*args):
        calls.append(True)
    async def send(event):
        sent.append(event)
    middleware = ApiAdmissionMiddleware(downstream)
    for forwarded in (b'1.1.1.1', b'2.2.2.2'):
        await middleware(scope(limits, [(b'x-forwarded-for', forwarded)]), receive_empty, send)
    assert len(calls) == 1 and sent[0]['status'] == 429


@pytest.mark.asyncio
async def test_untrusted_cors_preflight_is_rejected():
    from app.main import create_app
    app = create_app()
    app.state.api_admission = gate()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://demo') as client:
        response = await client.options('/api/v1/chat/messages', headers={
            'Origin': 'https://attacker.example', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type'})
    assert response.status_code == 400
    assert 'access-control-allow-origin' not in response.headers
    assert app.state.api_admission.active == 0
