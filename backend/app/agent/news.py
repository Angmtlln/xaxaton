"""OpenRouter web plugin and external-news hydration; no internal facts or LLM calls."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from app.config import Settings
from .models import ExternalNews, MasterAnswer

log = logging.getLogger(__name__)
DIRECTORIES = {"rusprofile.ru", "checko.ru", "list-org.com", "zachestnyibiznes.ru"}


def news_search_request(company: dict, days: int) -> tuple[dict, str]:
    from .prompt import NEWS_SELECTION_INSTRUCTIONS
    today = datetime.now(timezone.utc).date()
    identity = {key: company.get(key) for key in ("inn", "name", "full_name", "address")}
    return {
        "id": "web", "engine": "exa", "max_results": 8,
        "exclude_domains": sorted(DIRECTORIES),
        # This is the result-injection prompt, NOT a custom query parameter.
        "search_prompt": NEWS_SELECTION_INSTRUCTIONS,
    }, (
        "Дополнение к запросу полной проверки: автоматически найди важные новости "
        "именно этой компании за период "
        f"{(today - timedelta(days=days)).isoformat()} — {today.isoformat()}. "
        "Ищи по названию и ИНН; проверь совпадение компании по региону и контексту. "
        "Банкротство, существенные суды, расследования, санкции, крупные контракты "
        "и проекты, корпоративные изменения, аварии и срывы. "
        "Верни внутренний анализ в message, а 0–4 внешние новости отдельно в "
        "news_selection по схеме. Доверенная идентичность компании: "
        + json.dumps(identity, ensure_ascii=False)
    )


def _article_url(value: str) -> str:
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if (parts.scheme not in {"https", "http"} or not host or parts.username
            or parts.password or "." not in host or parts.port not in {None, 80, 443}
            or host.endswith((".local", ".localhost"))):
        raise ValueError("Not a public article URL")
    if any(host == domain or host.endswith("." + domain) for domain in DIRECTORIES):
        raise ValueError("Company directory")
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid", "yclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(sorted(query)), ""))


class _PublicationMetadata(HTMLParser):
    """Read publication metadata, never infer a date from arbitrary article prose."""

    def __init__(self):
        super().__init__()
        self.dates = []
        self.scripts = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if key in {"article:published_time", "datePublished", "datepublished"}:
                self.dates.append(attrs.get("content", ""))
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None

    def published(self) -> date | None:
        values = list(self.dates)
        if not values:
            for script in self.scripts:
                try:
                    data = json.loads(script)
                except (ValueError, RecursionError):
                    continue
                nodes = data if isinstance(data, list) else [data]
                for node in nodes:
                    if isinstance(node, dict):
                        items = node.get("@graph", [node])
                        if not isinstance(items, list):
                            continue
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            kinds = item.get("@type", [])
                            kinds = [kinds] if isinstance(kinds, str) else kinds
                            if isinstance(kinds, list) and any(kind in {"Article", "NewsArticle", "ReportageNewsArticle"} for kind in kinds if isinstance(kind, str)):
                                values.append(item.get("datePublished", ""))
        dates = set()
        for value in values:
            try:
                dates.add(datetime.fromisoformat(value.replace("Z", "+00:00")).date())
            except (ValueError, AttributeError, TypeError):
                continue
        # Ambiguous metadata is not permission to pick the newest date.
        return next(iter(dates)) if len(dates) == 1 else None


async def _publication_date(url: str, client: httpx.AsyncClient) -> date | None:
    for _ in range(4):
        url = _article_url(url)
        parts = urlsplit(url)
        addresses = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("Non-public destination")
        # Pin the validated address: a second DNS resolution must not turn a
        # public citation into a request to the local network (DNS rebinding).
        target = httpx.URL(url).copy_with(host=addresses[0][4][0])
        async with client.stream(
            "GET", target, headers={"Host": parts.netloc},
            extensions={"sni_hostname": parts.hostname}, follow_redirects=False,
        ) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", "").lower():
                return None
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 512_000:
                    break
            parser = _PublicationMetadata()
            parser.feed(content[:512_000].decode(response.encoding or "utf-8", errors="replace"))
            return parser.published()
    return None


async def hydrate_news(annotations: list, answer: MasterAnswer | None, *,
                       requested: bool, settings: Settings) -> tuple[list[ExternalNews], str]:
    if not requested:
        return [], "not_configured" if settings.llm_mock or not settings.openrouter_api_key else "unavailable"
    if answer is None or answer.news_selection is None:
        return [], "selection_unavailable"
    if not answer.news_selection:
        return [], "completed"
    if not isinstance(annotations, list):
        return [], "partial"
    citations = {}
    for annotation in annotations[:25]:
        try:
            if annotation["type"] != "url_citation":
                continue
            item = annotation["url_citation"]
            url = _article_url(item["url"])
            if isinstance(item.get("content"), str) and item["content"].strip():
                citations[url] = item
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    today = datetime.now(timezone.utc).date()
    pending, seen_titles, seen_urls = [], set(), set()
    async with httpx.AsyncClient(timeout=settings.web_news_timeout_s, trust_env=False) as client:
        async def hydrate(choice, url, citation):
            try:
                async with asyncio.timeout(settings.web_news_timeout_s):
                    published = await _publication_date(url, client)
                if published is None:
                    return None, True
                if not today - timedelta(days=settings.web_news_days) <= published <= today:
                    return None, False
                return ExternalNews(title=citation["title"], date=published,
                                    source=urlsplit(url).hostname, url=url, summary=choice.summary), False
            except (httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError, ValidationError):
                return None, True
        rejected = False
        for choice in answer.news_selection:
            try:
                url = _article_url(str(choice.url))
                citation = citations[url]
                title = " ".join(citation["title"].split()).casefold()
                if not title:
                    raise ValueError("Missing title")
            except (ValueError, TypeError, KeyError, AttributeError):
                rejected = True
                continue
            if url in seen_urls or title in seen_titles:
                continue
            seen_urls.add(url)
            seen_titles.add(title)
            pending.append(hydrate(choice, url, citation))
        hydrated = await asyncio.gather(*pending)
    news = [item for item, _ in hydrated if item is not None]
    status = "partial" if rejected or any(failed for _, failed in hydrated) else "completed"
    log.info("external_news status=%s selected=%s returned=%s", status, len(answer.news_selection), len(news))
    return news, status
