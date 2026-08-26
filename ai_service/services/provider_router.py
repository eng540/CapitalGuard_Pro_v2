"""Configuration-driven provider/model routing for the AI parsing service."""
from __future__ import annotations

import json
import logging
import os
import time
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from services.parsing_utils import _build_openai_headers, redact_sensitive_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderRoute:
    provider: str
    model: str
    api_url: str
    api_key: str
    protocol: str = "openai"
    capabilities: frozenset[str] = frozenset({"text", "vision"})
    priority: int = 100
    name: str = ""

    @property
    def route_name(self) -> str:
        return self.name or f"{self.provider}:{self.model}"

    def headers(self) -> Dict[str, str]:
        if self.protocol == "fal":
            return {"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"}
        return _build_openai_headers(self.api_key)


@dataclass
class _Circuit:
    failures: int = 0
    opened_until: float = 0.0


class ProviderRouter:
    """Select enabled routes and temporarily isolate unhealthy providers."""

    def __init__(self, routes: Iterable[ProviderRoute], failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.routes = tuple(sorted(routes, key=lambda route: (route.priority, route.provider, route.model)))
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        try:
            self.permanent_cooldown_seconds = max(
                self.cooldown_seconds,
                float(os.getenv("AI_CIRCUIT_PERMANENT_COOLDOWN_SECONDS", "900")),
            )
        except ValueError:
            self.permanent_cooldown_seconds = max(self.cooldown_seconds, 900.0)
        self._circuits: Dict[str, _Circuit] = {}

    @classmethod
    def from_env(cls) -> "ProviderRouter":
        routes = _routes_from_json_env()
        if not routes:
            routes = _legacy_routes_from_env()
        try:
            threshold = int(os.getenv("AI_CIRCUIT_FAILURE_THRESHOLD", "3"))
        except ValueError:
            threshold = 3
        try:
            cooldown = float(os.getenv("AI_CIRCUIT_COOLDOWN_SECONDS", "30"))
        except ValueError:
            cooldown = 30.0
        return cls(routes, threshold, cooldown)

    def routes_for(self, capability: str) -> List[ProviderRoute]:
        now = time.monotonic()
        selected: List[ProviderRoute] = []
        for route in self.routes:
            if capability not in route.capabilities:
                continue
            circuit = self._circuits.get(route.route_name)
            if circuit and circuit.opened_until > now:
                continue
            if circuit and circuit.opened_until:
                circuit.opened_until = 0.0
                circuit.failures = 0
            selected.append(route)
        return selected

    def record_success(self, route: ProviderRoute) -> None:
        self._circuits.pop(route.route_name, None)

    def record_failure(self, route: ProviderRoute, status: int) -> None:
        circuit = self._circuits.setdefault(route.route_name, _Circuit())
        circuit.failures += 1
        permanent_failure = status in {400, 401, 402, 403, 404, 422}
        if permanent_failure or circuit.failures >= self.failure_threshold:
            cooldown = self.permanent_cooldown_seconds if permanent_failure else self.cooldown_seconds
            circuit.opened_until = time.monotonic() + cooldown
            log.warning(
                "AI route circuit opened route=%s status=%s cooldown_seconds=%s permanent=%s",
                route.route_name,
                status,
                cooldown,
                permanent_failure,
            )

    def public_status(self) -> List[Dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "provider": route.provider,
                "model": route.model,
                "capabilities": sorted(route.capabilities),
                "priority": route.priority,
                "enabled": bool(route.api_key and route.api_url and route.model),
                "circuit_open": bool(self._circuits.get(route.route_name) and self._circuits[route.route_name].opened_until > now),
            }
            for route in self.routes
        ]


def _routes_from_json_env() -> List[ProviderRoute]:
    raw = os.getenv("AI_MODEL_ROUTES", "").strip()
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        log.error("AI_MODEL_ROUTES is not valid JSON; ignoring it.")
        return []
    if not isinstance(entries, list):
        log.error("AI_MODEL_ROUTES must be a JSON array; ignoring it.")
        return []

    routes: List[ProviderRoute] = []
    for index, entry in enumerate(entries[:20]):
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", "")).strip().lower()
        model = str(entry.get("model", "")).strip()
        api_url = str(entry.get("api_url", "")).strip()
        key_env = str(entry.get("api_key_env", "")).strip()
        api_key = os.getenv(key_env, "") if key_env else ""
        protocol = str(entry.get("protocol", "openai")).strip().lower()
        capabilities = frozenset(str(value).strip().lower() for value in entry.get("capabilities", ["text", "vision"]))
        try:
            priority = int(entry.get("priority", index * 10 + 10))
        except (TypeError, ValueError):
            priority = index * 10 + 10
        if provider and model and api_url and api_key and _is_allowed_route(provider, protocol, api_url):
            routes.append(ProviderRoute(provider, model, api_url, api_key, protocol, capabilities, priority, str(entry.get("name", ""))))
        elif provider and api_url and not _is_allowed_route(provider, protocol, api_url):
            log.error("Ignoring disallowed AI route provider=%s url=%s", provider, redact_sensitive_text(api_url))
    return routes


def _is_allowed_route(provider: str, protocol: str, api_url: str) -> bool:
    host = (urlparse(api_url).hostname or "").lower()
    if protocol == "fal" or provider == "fal":
        return host == "fal.run" or host.endswith(".fal.run")
    if provider == "openrouter":
        return host == "openrouter.ai" or host.endswith(".openrouter.ai")
    if provider == "huggingface":
        return host == "router.huggingface.co" or host.endswith(".huggingface.co")
    return os.getenv("AI_ALLOW_CUSTOM_PROVIDER_URLS", "0").lower() in {"1", "true", "yes"}


def _legacy_routes_from_env() -> List[ProviderRoute]:
    order = [value.strip().lower() for value in os.getenv("AI_PROVIDER_ORDER", "openrouter,huggingface,fal").split(",") if value.strip()]
    routes: List[ProviderRoute] = []
    for index, provider in enumerate(order):
        if provider == "openrouter":
            key = os.getenv("LLM_API_KEY", "")
            url = os.getenv("LLM_API_URL", "")
            model = os.getenv("LLM_MODEL", "")
            protocol = "openai"
        elif provider == "huggingface":
            key = os.getenv("HUGGINGFACE_TOKEN", "")
            url = os.getenv("HUGGINGFACE_API_URL", "https://router.huggingface.co/v1/chat/completions")
            model = os.getenv("HUGGINGFACE_MODEL", "")
            protocol = "openai"
        elif provider == "fal":
            key = os.getenv("FAL_KEY", "")
            model = os.getenv("FAL_MODEL", "")
            url = os.getenv("FAL_API_URL", "") or (f"https://fal.run/{model}" if model else "")
            protocol = "fal"
        else:
            log.warning("Ignoring unsupported AI provider=%s", redact_sensitive_text(provider))
            continue
        if key and url and model:
            routes.append(ProviderRoute(provider, model, url, key, protocol, frozenset({"text", "vision"}), index * 10 + 10))
    return routes


def router_enabled() -> bool:
    return os.getenv("AI_ROUTER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def extract_text_response(body: Any) -> str:
    """Extract text from common OpenAI/HF/FAL response envelopes."""
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        for item in body:
            text = extract_text_response(item)
            if text:
                return text
        return ""
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if choices:
        text = extract_text_response(choices[0])
        if text:
            return text
    for key in ("message", "content", "text", "output", "result", "response"):
        if key in body:
            text = extract_text_response(body[key])
            if text:
                return text
    return ""


_ROUTER: Optional[ProviderRouter] = None
_ROUTER_FINGERPRINT: Optional[tuple[str, ...]] = None


def get_provider_router() -> ProviderRouter:
    """Return a process-local router so circuit state survives requests."""
    global _ROUTER, _ROUTER_FINGERPRINT
    fingerprint = tuple(os.getenv(name, "") for name in (
        "AI_MODEL_ROUTES",
        "AI_PROVIDER_ORDER",
        "AI_CIRCUIT_FAILURE_THRESHOLD",
        "AI_CIRCUIT_COOLDOWN_SECONDS",
        "AI_CIRCUIT_PERMANENT_COOLDOWN_SECONDS",
        "LLM_API_URL",
        "LLM_MODEL",
        "HUGGINGFACE_API_URL",
        "HUGGINGFACE_MODEL",
        "FAL_API_URL",
        "FAL_MODEL",
    ))
    if _ROUTER is None or fingerprint != _ROUTER_FINGERPRINT:
        _ROUTER = ProviderRouter.from_env()
        _ROUTER_FINGERPRINT = fingerprint
    return _ROUTER
