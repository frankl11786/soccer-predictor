from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .utils import as_float, normalize_name, utc_now_iso

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"


@dataclass(frozen=True)
class MarketQuote:
    probability: float
    market_id: str
    question: str
    event_title: str
    event_slug: str
    liquidity: float
    volume: float
    updated_at: str


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _yes_probability(market: dict[str, Any]) -> float | None:
    outcomes = [str(v).lower() for v in _json_list(market.get("outcomes"))]
    prices = _json_list(market.get("outcomePrices"))
    if outcomes and len(outcomes) == len(prices):
        for index, outcome in enumerate(outcomes):
            if outcome == "yes":
                price = as_float(prices[index], -1)
                if 0 <= price <= 1:
                    return price
    token_ids = _json_list(market.get("clobTokenIds"))
    if outcomes and token_ids and len(outcomes) == len(token_ids):
        for index, outcome in enumerate(outcomes):
            if outcome == "yes":
                try:
                    response = requests.get(
                        f"{CLOB_URL}/midpoint",
                        params={"token_id": token_ids[index]},
                        timeout=15,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    price = as_float(payload.get("mid") or payload.get("price"), -1)
                    if 0 <= price <= 1:
                        return price
                except requests.RequestException:
                    pass
    return None


def _market_text(market: dict[str, Any]) -> str:
    return " ".join(
        str(market.get(key) or "")
        for key in ("groupItemTitle", "question", "title", "slug")
    )


def _match_score(team_name: str, market: dict[str, Any]) -> int:
    team = normalize_name(team_name)
    text = normalize_name(_market_text(market))
    if not team or not text:
        return 0
    if team in text:
        return 100 + len(team)
    tokens = [token for token in team_name.lower().replace(".", "").split() if len(token) > 3]
    return sum(10 for token in tokens if normalize_name(token) in text)


def fetch_winner_quotes(queries: tuple[str, ...], team_names: list[str]) -> tuple[dict[str, MarketQuote], dict[str, Any]]:
    events_by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for query in queries:
        try:
            response = requests.get(
                f"{GAMMA_URL}/public-search",
                params={
                    "q": query,
                    "events_status": "active",
                    "limit_per_type": 20,
                    "keep_closed_markets": 0,
                    "search_profiles": False,
                },
                timeout=25,
            )
            response.raise_for_status()
            for event in response.json().get("events") or []:
                if event.get("active") is False or event.get("closed") is True:
                    continue
                events_by_id[str(event.get("id"))] = event
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{query}: {exc}")

    quotes: dict[str, MarketQuote] = {}
    for team_name in team_names:
        candidates: list[tuple[int, float, MarketQuote]] = []
        for event in events_by_id.values():
            for market in event.get("markets") or []:
                if market.get("active") is False or market.get("closed") is True:
                    continue
                score = _match_score(team_name, market)
                if score < 20:
                    continue
                probability = _yes_probability(market)
                if probability is None:
                    continue
                liquidity = as_float(market.get("liquidityNum") or market.get("liquidity"), 0)
                volume = as_float(market.get("volumeNum") or market.get("volume"), 0)
                quote = MarketQuote(
                    probability=probability,
                    market_id=str(market.get("id") or ""),
                    question=str(market.get("question") or market.get("groupItemTitle") or ""),
                    event_title=str(event.get("title") or ""),
                    event_slug=str(event.get("slug") or ""),
                    liquidity=liquidity,
                    volume=volume,
                    updated_at=utc_now_iso(),
                )
                candidates.append((score, liquidity + volume * 0.001, quote))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            quotes[team_name] = candidates[0][2]

    metadata = {
        "source": "Polymarket",
        "queries": list(queries),
        "events_checked": len(events_by_id),
        "quotes_found": len(quotes),
        "errors": errors,
        "updated_at": utc_now_iso(),
    }
    return quotes, metadata
