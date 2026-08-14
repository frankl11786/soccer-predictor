from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .polymarket import MARKET_ALIASES
from .utils import as_float, normalize_name, utc_now_iso

API_BASE = "https://external-api.kalshi.com/trade-api/v2"
WEB_BASE = "https://kalshi.com/markets"
USER_AGENT = "TouchlineForecast/1.0 (+soccer prediction market comparison)"


KALSHI_ALIASES: dict[str, tuple[str, ...]] = {
    # Keys use normalize_name(), which removes common club suffixes such as
    # FC/CF/SC. Kalshi intentionally shortens several MLS labels, so these
    # exchange-specific aliases keep matching precise without loosening the
    # more general Polymarket matcher.
    "intermiami": ("Miami", "Inter Miami"),
    "losangeles": ("Los Angeles F", "LAFC"),
    "lagalaxy": ("Los Angeles G", "LA Galaxy"),
    "sportingkansascity": ("Kansas City", "Sporting Kansas City"),
    "realsaltlake": ("Salt Lake", "Real Salt Lake"),
    "stlouiscity": ("Saint Louis", "St. Louis", "St Louis"),
    "redbullnewyork": ("New York RB", "New York Red Bulls", "Red Bulls", "NYRB"),
    "newyorkcity": ("New York City", "NYCFC"),
    "montreal": ("Montreal", "CF Montreal"),
    "seattlesounders": ("Seattle", "Seattle Sounders"),
    "vancouverwhitecaps": ("Vancouver", "Vancouver Whitecaps"),
    "houstondynamo": ("Houston", "Houston Dynamo"),
    "minnesotaunited": ("Minnesota", "Minnesota United"),
    "orlandocity": ("Orlando", "Orlando City"),
    "chicagofire": ("Chicago Fire", "Chicago"),
}

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class KalshiPrice:
    probability: float
    bid: float | None
    ask: float | None
    last: float | None
    spread: float | None
    method: str


@dataclass(frozen=True)
class KalshiWinnerQuote:
    probability: float
    raw_probability: float
    normalized: bool
    normalization_total: float | None
    bid: float | None
    ask: float | None
    last: float | None
    spread: float | None
    estimate_method: str
    market_ticker: str
    event_ticker: str
    event_title: str
    event_url: str
    volume: float
    volume_24h: float
    liquidity: float
    open_interest: float
    updated_at: str


@dataclass(frozen=True)
class KalshiMatchQuote:
    fixture_id: str
    home_probability: float
    draw_probability: float
    away_probability: float
    home_raw_probability: float
    draw_raw_probability: float
    away_raw_probability: float
    normalized: bool
    normalization_total: float
    event_ticker: str
    event_title: str
    event_url: str
    kickoff: str | None
    market_tickers: dict[str, str]
    bids: dict[str, float | None]
    asks: dict[str, float | None]
    lasts: dict[str, float | None]
    spreads: dict[str, float | None]
    estimate_methods: dict[str, str]
    volume: float
    volume_24h: float
    liquidity: float
    open_interest: float
    updated_at: str


def _request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{API_BASE}{path}",
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Kalshi returned a non-object JSON response")
    return payload


def _dollar_price(market: dict[str, Any], dollar_key: str, cents_key: str) -> float | None:
    raw = market.get(dollar_key)
    if raw is not None and str(raw).strip() != "":
        value = as_float(raw, -1.0)
        if 0.0 <= value <= 1.0:
            return value
    raw = market.get(cents_key)
    if raw is not None and str(raw).strip() != "":
        value = as_float(raw, -1.0)
        # Legacy non-dollar Kalshi price fields are denominated in cents.
        # In particular, a value of 1 means one cent (1%), not probability 1.0.
        if 0.0 <= value <= 100.0:
            return value / 100.0
    return None


def _market_price(market: dict[str, Any]) -> KalshiPrice | None:
    bid = _dollar_price(market, "yes_bid_dollars", "yes_bid")
    ask = _dollar_price(market, "yes_ask_dollars", "yes_ask")
    last = _dollar_price(market, "last_price_dollars", "last_price")

    if bid is not None and ask is not None and bid <= ask:
        spread = ask - bid
        # A genuine two-sided or one-tick-wide book is the best estimate. For
        # a very wide book, the last trade is less misleading than the midpoint.
        if spread <= 0.25 and not (bid == 0.0 and ask == 1.0):
            return KalshiPrice(
                probability=(bid + ask) / 2.0,
                bid=bid,
                ask=ask,
                last=last,
                spread=spread,
                method="bid_ask_midpoint",
            )

    if last is not None and 0.0 <= last <= 1.0:
        spread = (ask - bid) if bid is not None and ask is not None and bid <= ask else None
        return KalshiPrice(
            probability=last,
            bid=bid,
            ask=ask,
            last=last,
            spread=spread,
            method="last_trade",
        )

    if bid is not None and ask is not None and bid <= ask and not (bid == 0.0 and ask == 1.0):
        return KalshiPrice(
            probability=(bid + ask) / 2.0,
            bid=bid,
            ask=ask,
            last=last,
            spread=ask - bid,
            method="wide_bid_ask_midpoint",
        )
    return None


def _team_aliases(team_name: str, short: str = "") -> tuple[str, ...]:
    key = normalize_name(team_name)
    aliases: list[str] = [team_name]
    aliases.extend(MARKET_ALIASES.get(key, ()))
    aliases.extend(KALSHI_ALIASES.get(key, ()))
    if short and len(short.strip()) >= 2:
        aliases.append(short.strip())
    generic = re.sub(r"\b(FC|CF|SC|AFC)\b", "", team_name, flags=re.IGNORECASE).strip()
    if generic and generic != team_name:
        aliases.append(generic)
    if team_name.endswith(" United") and len(team_name.split()) > 2:
        aliases.append(team_name[: -len(" United")])
    if team_name.endswith(" City") and len(team_name.split()) > 2:
        aliases.append(team_name[: -len(" City")])

    unique: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        normalized = normalize_name(alias)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(alias)
    return tuple(unique)


def _alias_score(text: str, aliases: tuple[str, ...]) -> int:
    normalized_text = normalize_name(text)
    best = 0
    for alias in aliases:
        normalized_alias = normalize_name(alias)
        if normalized_alias and normalized_alias in normalized_text:
            best = max(best, 100 + len(normalized_alias))
    return best


def _market_text(market: dict[str, Any]) -> str:
    return " ".join(
        str(market.get(key) or "")
        for key in ("yes_sub_title", "subtitle", "title", "ticker")
    )


def _event_text(event: dict[str, Any]) -> str:
    parts = [str(event.get(key) or "") for key in ("title", "sub_title", "event_ticker")]
    parts.extend(_market_text(market) for market in event.get("markets") or [])
    return " ".join(parts)


def _team_market_score(team_name: str, market: dict[str, Any]) -> int:
    return _alias_score(_market_text(market), _team_aliases(team_name))


def _market_float(market: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = market.get(key)
        if value is None:
            continue
        parsed = as_float(value, -1.0)
        if parsed >= 0:
            return parsed
    return 0.0


def _event_url(series_ticker: str, event_ticker: str) -> str:
    series = series_ticker.upper()
    event = event_ticker.lower()
    if series == "KXPREMIERLEAGUE":
        return f"{WEB_BASE}/kxpremierleague/premier-league/{event}"
    if series == "KXEPLGAME":
        return f"{WEB_BASE}/kxeplgame/english-premier-league-game/{event}"
    if series == "KXMLSCUP":
        return f"{WEB_BASE}/kxmlscup/mls-cup-champion/{event}"
    if series == "KXMLSGAME":
        return f"{WEB_BASE}/kxmlsgame/major-league-soccer-game/{event}"
    return f"{WEB_BASE}/{series_ticker.lower()}/"


def _updated_at(market: dict[str, Any], event: dict[str, Any]) -> str:
    return str(
        market.get("updated_time")
        or event.get("last_updated_ts")
        or event.get("updated_time")
        or utc_now_iso()
    )


def fetch_winner_quotes(
    event_ticker: str | None,
    team_names: list[str],
) -> tuple[dict[str, KalshiWinnerQuote], dict[str, Any]]:
    errors: list[str] = []
    if not event_ticker:
        return {}, {
            "source": "Kalshi",
            "market_type": "season_winner",
            "requested_event_ticker": None,
            "quotes_found": 0,
            "errors": ["No exact Kalshi season-winner event ticker configured"],
            "updated_at": utc_now_iso(),
        }

    try:
        payload = _request_json(
            f"/events/{event_ticker}",
            params={"with_nested_markets": "true"},
        )
        event = payload.get("event") or {}
        if not isinstance(event, dict):
            raise ValueError("Kalshi event response did not include an event object")
        # Kalshi's Get Event endpoint historically returned markets at the
        # response top level unless nested markets were requested. Accept both
        # shapes so a harmless API response-shape change cannot silently erase
        # all season quotes.
        if not event.get("markets") and isinstance(payload.get("markets"), list):
            event = {**event, "markets": payload.get("markets") or []}
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {}, {
            "source": "Kalshi",
            "market_type": "season_winner",
            "requested_event_ticker": event_ticker,
            "quotes_found": 0,
            "errors": [f"event {event_ticker}: {exc}"],
            "updated_at": utc_now_iso(),
        }

    markets = [
        market
        for market in (event.get("markets") or [])
        if isinstance(market, dict) and str(market.get("status") or "open").lower() not in {"settled", "closed"}
    ]
    priced: list[tuple[dict[str, Any], KalshiPrice]] = []
    for market in markets:
        price = _market_price(market)
        if price is not None:
            priced.append((market, price))

    raw_total = sum(price.probability for _, price in priced)
    normalized = len(priced) >= max(5, int(len(markets) * 0.70)) and 0.50 <= raw_total <= 2.00
    series_ticker = str(event.get("series_ticker") or event_ticker.split("-")[0])
    event_title = str(event.get("title") or "")
    url = _event_url(series_ticker, event_ticker)

    quotes: dict[str, KalshiWinnerQuote] = {}
    for team_name in team_names:
        candidates: list[tuple[int, float, dict[str, Any], KalshiPrice]] = []
        for market, price in priced:
            score = _team_market_score(team_name, market)
            if score < 100:
                continue
            volume = _market_float(market, "volume_fp", "volume")
            liquidity = _market_float(market, "liquidity_dollars", "liquidity")
            quality = volume + liquidity
            candidates.append((score, quality, market, price))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, _, market, price = candidates[0]
        probability = price.probability / raw_total if normalized and raw_total else price.probability
        quotes[team_name] = KalshiWinnerQuote(
            probability=probability,
            raw_probability=price.probability,
            normalized=normalized,
            normalization_total=raw_total if normalized else None,
            bid=price.bid,
            ask=price.ask,
            last=price.last,
            spread=price.spread,
            estimate_method=price.method,
            market_ticker=str(market.get("ticker") or ""),
            event_ticker=str(event.get("event_ticker") or event_ticker),
            event_title=event_title,
            event_url=url,
            volume=_market_float(market, "volume_fp", "volume"),
            volume_24h=_market_float(market, "volume_24h_fp", "volume_24h"),
            liquidity=_market_float(market, "liquidity_dollars", "liquidity"),
            open_interest=_market_float(market, "open_interest_fp", "open_interest"),
            updated_at=_updated_at(market, event),
        )

    matched = len(quotes)
    if normalized and matched < max(5, int(len(markets) * 0.60)):
        # Keep the market prices raw when our team-name mapping does not cover
        # enough of the event to make the normalization auditable.
        normalized = False
        quotes = {
            name: replace(quote, probability=quote.raw_probability, normalized=False, normalization_total=None)
            for name, quote in quotes.items()
        }

    metadata = {
        "source": "Kalshi",
        "market_type": "season_winner",
        "requested_event_ticker": event_ticker,
        "event_ticker": str(event.get("event_ticker") or event_ticker),
        "series_ticker": series_ticker,
        "event_title": event_title,
        "event_url": url,
        "mutually_exclusive": event.get("mutually_exclusive"),
        "contracts": len(markets),
        "priced_contracts": len(priced),
        "quotes_found": len(quotes),
        "normalization_applied": normalized,
        "normalization_total": round(raw_total, 6) if raw_total else None,
        "price_method": "bid/ask midpoint when usable; otherwise last trade",
        "errors": errors,
        "updated_at": utc_now_iso(),
    }
    return quotes, metadata


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _ticker_date(event_ticker: str) -> date | None:
    match = re.search(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", event_ticker.upper())
    if not match:
        return None
    year = 2000 + int(match.group(1))
    month = MONTHS[match.group(2)]
    day = int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    ticker_date = _ticker_date(str(event.get("event_ticker") or ""))
    markets = [market for market in event.get("markets") or [] if isinstance(market, dict)]
    for market in markets:
        for key in ("occurrence_datetime", "expected_expiration_time", "close_time", "expiration_time", "latest_expiration_time"):
            parsed = _parse_datetime(market.get(key))
            if parsed:
                return parsed
    for key in ("strike_date", "last_updated_ts"):
        parsed = _parse_datetime(event.get(key))
        if parsed:
            return parsed
    if ticker_date:
        return datetime(ticker_date.year, ticker_date.month, ticker_date.day, 12, tzinfo=timezone.utc)
    return None


def _fixture_datetime(fixture: dict[str, Any]) -> datetime | None:
    parsed = _parse_datetime(fixture.get("kickoff"))
    if parsed:
        return parsed
    value = fixture.get("date")
    if value:
        try:
            d = date.fromisoformat(str(value)[:10])
            return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _classify_market(
    market: dict[str, Any],
    home_aliases: tuple[str, ...],
    away_aliases: tuple[str, ...],
) -> str | None:
    # Do not classify from the full ticker. Kalshi game-market tickers embed
    # both club codes (for example ATLNYRB), so a short code such as ATL can
    # otherwise make the opposing outcome look like it mentions both teams.
    labels = [
        str(market.get("yes_sub_title") or "").strip(),
        str(market.get("subtitle") or "").strip(),
        str(market.get("title") or "").strip(),
    ]
    ticker = str(market.get("ticker") or "")
    if ticker:
        labels.append(ticker.rsplit("-", 1)[-1])

    for label in labels:
        if not label:
            continue
        normalized = normalize_name(label)
        if normalized in {"tie", "draw"} or "draw" in normalized:
            return "draw"
        home_score = _alias_score(label, home_aliases)
        away_score = _alias_score(label, away_aliases)
        if home_score and not away_score:
            return "home"
        if away_score and not home_score:
            return "away"
    return None


def _merge_market_lists(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for market in [*existing, *incoming]:
        if not isinstance(market, dict):
            continue
        ticker = str(market.get("ticker") or "")
        if ticker:
            merged[ticker] = {**merged.get(ticker, {}), **market}
        else:
            anonymous.append(market)
    return [*merged.values(), *anonymous]


def _upcoming_events(
    series_ticker: str,
    min_close_ts: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    """Discover upcoming Kalshi game events without relying on one status value.

    Kalshi's event and market list endpoints both support a series filter.  We
    query events with nested markets first, then supplement them from the
    markets endpoint.  The second path protects against an event being omitted
    by a lifecycle/status interpretation or a nested-market response change.
    """

    errors: list[str] = []
    events_by_ticker: dict[str, dict[str, Any]] = {}
    event_rows = 0
    market_rows = 0

    cursor = ""
    for _ in range(5):
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "min_close_ts": min_close_ts,
            "with_nested_markets": "true",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        try:
            payload = _request_json("/events", params=params)
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"events {series_ticker}: {exc}")
            break

        page = payload.get("events") or []
        for event in page:
            if not isinstance(event, dict):
                continue
            event_rows += 1
            ticker = str(event.get("event_ticker") or "")
            if not ticker:
                continue
            previous = events_by_ticker.get(ticker, {})
            events_by_ticker[ticker] = {
                **previous,
                **event,
                "markets": _merge_market_lists(
                    list(previous.get("markets") or []),
                    list(event.get("markets") or []),
                ),
            }
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break

    # Independent discovery path. Do not set a status filter: Kalshi documents
    # min_close_ts for this endpoint with an empty status, and this lets our
    # local team/date/result checks decide whether a returned market is usable.
    cursor = ""
    for _ in range(5):
        params = {
            "series_ticker": series_ticker,
            "min_close_ts": min_close_ts,
            "mve_filter": "exclude",
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor
        try:
            payload = _request_json("/markets", params=params)
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"markets {series_ticker}: {exc}")
            break

        page = payload.get("markets") or []
        for market in page:
            if not isinstance(market, dict):
                continue
            market_rows += 1
            event_ticker = str(market.get("event_ticker") or "")
            if not event_ticker:
                continue
            event = events_by_ticker.setdefault(
                event_ticker,
                {
                    "event_ticker": event_ticker,
                    "series_ticker": series_ticker,
                    "title": "",
                    "markets": [],
                },
            )
            event["markets"] = _merge_market_lists(
                list(event.get("markets") or []),
                [market],
            )
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break

    return list(events_by_ticker.values()), errors, {
        "event_rows": event_rows,
        "market_rows": market_rows,
        "merged_events": len(events_by_ticker),
    }

def fetch_match_quotes(
    fixtures: list[dict[str, Any]],
    teams: list[dict[str, Any]],
    series_ticker: str | None,
    lookahead_days: int = 21,
    max_fixtures: int = 60,
) -> tuple[dict[str, KalshiMatchQuote], dict[str, Any]]:
    if not series_ticker:
        return {}, {
            "source": "Kalshi",
            "market_type": "match_result",
            "series_ticker": None,
            "quotes_found": 0,
            "errors": ["No Kalshi game series ticker configured"],
            "updated_at": utc_now_iso(),
        }

    team_by_slug = {str(team.get("slug")): team for team in teams}
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, lookahead_days))
    candidates = []
    for fixture in fixtures:
        if fixture.get("status") == "final":
            continue
        kickoff = _fixture_datetime(fixture)
        if kickoff is None:
            continue
        if kickoff < now - timedelta(hours=12) or kickoff > horizon:
            continue
        candidates.append(fixture)
    candidates.sort(key=lambda fixture: _fixture_datetime(fixture) or horizon)
    candidates = candidates[:max_fixtures]

    discovery_min_close_ts = int((now - timedelta(hours=12)).timestamp())
    events, errors, discovery = _upcoming_events(series_ticker, discovery_min_close_ts)
    quotes: dict[str, KalshiMatchQuote] = {}
    used_events: set[str] = set()

    for fixture in candidates:
        home = team_by_slug.get(str(fixture.get("home")))
        away = team_by_slug.get(str(fixture.get("away")))
        if not home or not away:
            continue
        home_aliases = _team_aliases(str(home.get("name") or ""), str(home.get("short") or ""))
        away_aliases = _team_aliases(str(away.get("name") or ""), str(away.get("short") or ""))
        fixture_dt = _fixture_datetime(fixture)
        if fixture_dt is None:
            continue

        event_candidates: list[tuple[int, float, dict[str, Any]]] = []
        for event in events:
            event_ticker = str(event.get("event_ticker") or "")
            if event_ticker in used_events:
                continue
            text = _event_text(event)
            home_score = _alias_score(text, home_aliases)
            away_score = _alias_score(text, away_aliases)
            if not home_score or not away_score:
                continue
            event_dt = _event_datetime(event)
            if event_dt is None:
                continue
            ticker_day = _ticker_date(event_ticker)
            if ticker_day is not None:
                if abs((ticker_day - fixture_dt.date()).days) > 1:
                    continue
            elif abs((event_dt - fixture_dt).total_seconds()) > 36 * 3600:
                continue
            score = home_score + away_score
            if " vs " in str(event.get("title") or "").lower():
                score += 50
            distance = abs((event_dt - fixture_dt).total_seconds()) / 3600.0
            score += max(0, int(36 - min(distance, 36)))
            volume = sum(_market_float(m, "volume_fp", "volume") for m in event.get("markets") or [] if isinstance(m, dict))
            event_candidates.append((score, volume, event))

        if not event_candidates:
            continue
        event_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        event = event_candidates[0][2]
        event_ticker = str(event.get("event_ticker") or "")

        outcome_candidates: dict[str, list[tuple[float, dict[str, Any], KalshiPrice]]] = {"home": [], "draw": [], "away": []}
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            if str(market.get("status") or "open").lower() in {"settled", "closed"}:
                continue
            outcome = _classify_market(market, home_aliases, away_aliases)
            if not outcome:
                continue
            price = _market_price(market)
            if price is None:
                continue
            spread_quality = -(price.spread if price.spread is not None else 1.0)
            volume = _market_float(market, "volume_fp", "volume")
            outcome_candidates[outcome].append((spread_quality + volume * 1e-9, market, price))

        if any(not outcome_candidates[outcome] for outcome in ("home", "draw", "away")):
            continue

        selected: dict[str, tuple[dict[str, Any], KalshiPrice]] = {}
        for outcome in ("home", "draw", "away"):
            outcome_candidates[outcome].sort(key=lambda item: item[0], reverse=True)
            _, market, price = outcome_candidates[outcome][0]
            selected[outcome] = (market, price)

        raw = {outcome: selected[outcome][1].probability for outcome in ("home", "draw", "away")}
        total = sum(raw.values())
        if not (0.50 <= total <= 1.50):
            continue
        probabilities = {outcome: raw[outcome] / total for outcome in raw}
        market_list = [selected[outcome][0] for outcome in ("home", "draw", "away")]
        quote = KalshiMatchQuote(
            fixture_id=str(fixture.get("id") or ""),
            home_probability=probabilities["home"],
            draw_probability=probabilities["draw"],
            away_probability=probabilities["away"],
            home_raw_probability=raw["home"],
            draw_raw_probability=raw["draw"],
            away_raw_probability=raw["away"],
            normalized=abs(total - 1.0) > 0.00001,
            normalization_total=total,
            event_ticker=event_ticker,
            event_title=str(event.get("title") or ""),
            event_url=_event_url(series_ticker, event_ticker),
            kickoff=_event_datetime(event).isoformat() if _event_datetime(event) else None,
            market_tickers={outcome: str(selected[outcome][0].get("ticker") or "") for outcome in ("home", "draw", "away")},
            bids={outcome: selected[outcome][1].bid for outcome in ("home", "draw", "away")},
            asks={outcome: selected[outcome][1].ask for outcome in ("home", "draw", "away")},
            lasts={outcome: selected[outcome][1].last for outcome in ("home", "draw", "away")},
            spreads={outcome: selected[outcome][1].spread for outcome in ("home", "draw", "away")},
            estimate_methods={outcome: selected[outcome][1].method for outcome in ("home", "draw", "away")},
            volume=sum(_market_float(m, "volume_fp", "volume") for m in market_list),
            volume_24h=sum(_market_float(m, "volume_24h_fp", "volume_24h") for m in market_list),
            liquidity=sum(_market_float(m, "liquidity_dollars", "liquidity") for m in market_list),
            open_interest=sum(_market_float(m, "open_interest_fp", "open_interest") for m in market_list),
            updated_at=max((_updated_at(m, event) for m in market_list), default=utc_now_iso()),
        )
        quotes[str(fixture.get("id") or "")] = quote
        used_events.add(event_ticker)

    metadata = {
        "source": "Kalshi",
        "market_type": "match_result",
        "series_ticker": series_ticker,
        "events_checked": len(events),
        "discovery": discovery,
        "discovery_min_close_ts": discovery_min_close_ts,
        "fixtures_checked": len(candidates),
        "quotes_found": len(quotes),
        "coverage": (len(quotes) / len(candidates)) if candidates else 0.0,
        "lookahead_days": lookahead_days,
        "max_fixtures": max_fixtures,
        "price_method": "bid/ask midpoint when usable; otherwise last trade; 1X2 normalized to 100%",
        "discovery_method": "status-agnostic events plus markets fallback within the configured Kalshi series",
        "errors": errors,
        "updated_at": utc_now_iso(),
    }
    return quotes, metadata
