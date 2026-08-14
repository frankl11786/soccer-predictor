from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .utils import as_float, normalize_name, utc_now_iso

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
POLYMARKET_EVENT_URL = "https://polymarket.com/event"

# Display-name variants that commonly appear in Polymarket soccer events.
# Matching still requires both clubs and, when available, a compatible kickoff.
MARKET_ALIASES: dict[str, tuple[str, ...]] = {
    "atlantaunited": ("Atlanta", "Atlanta United"),
    "austin": ("Austin", "Austin FC"),
    "charlotte": ("Charlotte", "Charlotte FC"),
    "chicagofire": ("Chicago", "Chicago Fire"),
    "coloradorapids": ("Colorado", "Colorado Rapids"),
    "columbuscrew": ("Columbus", "Columbus Crew"),
    "dcunited": ("D.C. United", "DC United"),
    "fccincinnati": ("FC Cincinnati", "Cincinnati"),
    "intermiami": ("Inter Miami",),
    "lagalaxy": ("LA Galaxy", "Los Angeles Galaxy"),
    "losangeles": ("LAFC", "Los Angeles FC"),
    "minnesotaunited": ("Minnesota", "Minnesota United"),
    "montreal": ("CF Montreal", "Montreal"),
    "nashville": ("Nashville", "Nashville SC"),
    "newenglandrevolution": ("New England", "New England Revolution"),
    "newyorkcity": ("NYCFC", "New York City", "New York City FC"),
    "redbullnewyork": ("Red Bulls", "New York Red Bulls"),
    "orlandocity": ("Orlando", "Orlando City"),
    "philadelphiaunion": ("Philadelphia", "Philadelphia Union"),
    "portlandtimbers": ("Portland", "Portland Timbers"),
    "realsaltlake": ("Real Salt Lake",),
    "sandiego": ("San Diego", "San Diego FC"),
    "sanjoseearthquakes": ("San Jose", "San Jose Earthquakes"),
    "seattlesounders": ("Seattle", "Seattle Sounders"),
    "sportingkansascity": ("Kansas City", "Sporting Kansas City"),
    "stlouiscity": ("St. Louis City", "St Louis City"),
    "toronto": ("Toronto", "Toronto FC"),
    "vancouverwhitecaps": ("Vancouver", "Vancouver Whitecaps"),
    "brightonhovealbion": ("Brighton", "Brighton and Hove Albion"),
    "manchesterunited": ("Manchester United", "Man United"),
    "manchestercity": ("Manchester City", "Man City"),
    "newcastleunited": ("Newcastle", "Newcastle United"),
    "nottinghamforest": ("Nottingham Forest", "Nott'm Forest"),
    "tottenhamhotspur": ("Tottenham", "Tottenham Hotspur", "Spurs"),
    "westhamunited": ("West Ham", "West Ham United"),
    "wolves": ("Wolves", "Wolverhampton Wanderers"),
}

EXCLUDED_MATCH_MARKET_TERMS = (
    "spread",
    "handicap",
    "total",
    "overunder",
    "firsthalf",
    "1sthalf",
    "secondhalf",
    "2ndhalf",
    "corners",
    "cards",
    "bothteamstoscore",
    "exactscore",
    "correctscore",
    "winningmargin",
    "winby",
    "goals",
    "doublechance",
    "drawnobet",
    "qualify",
    "advance",
    "extratime",
    "penalties",
    "penaltyshootout",
    "trophy",
    "serieswinner",
)


@dataclass(frozen=True)
class MarketQuote:
    probability: float
    raw_probability: float
    normalized: bool
    normalization_total: float | None
    market_id: str
    event_id: str
    question: str
    event_title: str
    event_slug: str
    liquidity: float
    volume: float
    updated_at: str


@dataclass(frozen=True)
class MatchMarketQuote:
    fixture_id: str
    home_probability: float
    draw_probability: float
    away_probability: float
    home_raw_probability: float
    draw_raw_probability: float
    away_raw_probability: float
    normalized: bool
    normalization_total: float
    event_id: str
    event_title: str
    event_slug: str
    event_url: str
    kickoff: str | None
    market_ids: dict[str, str]
    questions: dict[str, str]
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
                except (requests.RequestException, ValueError):
                    pass
    return None


def _market_text(market: dict[str, Any]) -> str:
    return " ".join(
        str(market.get(key) or "")
        for key in ("groupItemTitle", "question", "title", "slug")
    )


def _winner_aliases(team_name: str) -> tuple[str, ...]:
    key = normalize_name(team_name)
    aliases: list[str] = [team_name]
    aliases.extend(MARKET_ALIASES.get(key, ()))
    generic = re.sub(r"\b(FC|CF|SC|AFC)\b", "", team_name, flags=re.IGNORECASE).strip()
    if generic and generic != team_name:
        aliases.append(generic)
    unique: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        normalized = normalize_name(alias)
        if len(normalized) < 3 or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(alias)
    return tuple(unique)


def _match_score(team_name: str, market: dict[str, Any]) -> int:
    text = normalize_name(_market_text(market))
    if not text:
        return 0
    best = 0
    for alias in _winner_aliases(team_name):
        normalized = normalize_name(alias)
        if normalized and normalized in text:
            best = max(best, 100 + len(normalized))
    if best:
        return best
    tokens = [token for token in team_name.lower().replace(".", "").split() if len(token) > 3]
    return sum(10 for token in tokens if normalize_name(token) in text)


def _active_events(
    queries: tuple[str, ...],
    event_slug: str | None,
) -> tuple[dict[str, dict[str, Any]], list[str], str | None]:
    events_by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    direct_slug_used: str | None = None

    if event_slug:
        try:
            response = requests.get(
                f"{GAMMA_URL}/events/slug/{event_slug}",
                timeout=25,
            )
            response.raise_for_status()
            event = response.json()
            if isinstance(event, dict) and event.get("active") is not False and event.get("closed") is not True:
                events_by_id[str(event.get("id") or event_slug)] = event
                direct_slug_used = event_slug
        except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
            errors.append(f"event slug {event_slug}: {exc}")

    # Broad search is used only when no exact slug was configured. If an exact
    # event lookup fails, publishing no comparison is safer than mixing prices
    # from similarly named season markets.
    if not events_by_id and not event_slug:
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
            except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
                errors.append(f"{query}: {exc}")

    return events_by_id, errors, direct_slug_used


def fetch_winner_quotes(
    queries: tuple[str, ...],
    team_names: list[str],
    event_slug: str | None = None,
) -> tuple[dict[str, MarketQuote], dict[str, Any]]:
    events_by_id, errors, direct_slug_used = _active_events(queries, event_slug)

    # Sum every active Yes contract in each event, including "Other". Binary
    # contracts in an exhaustive winner event often add to more than 100%, so
    # this total is required for comparable implied probabilities.
    event_totals: dict[str, float] = {}
    event_market_counts: dict[str, int] = {}
    for event_id, event in events_by_id.items():
        total = 0.0
        count = 0
        for market in event.get("markets") or []:
            if market.get("active") is False or market.get("closed") is True:
                continue
            probability = _yes_probability(market)
            if probability is None:
                continue
            total += probability
            count += 1
        event_totals[event_id] = total
        event_market_counts[event_id] = count

    quotes: dict[str, MarketQuote] = {}
    for team_name in team_names:
        candidates: list[tuple[int, float, MarketQuote]] = []
        for event_id, event in events_by_id.items():
            for market in event.get("markets") or []:
                if market.get("active") is False or market.get("closed") is True:
                    continue
                score = _match_score(team_name, market)
                if score < 20:
                    continue
                raw_probability = _yes_probability(market)
                if raw_probability is None:
                    continue
                liquidity = as_float(market.get("liquidityNum") or market.get("liquidity"), 0)
                volume = as_float(market.get("volumeNum") or market.get("volume"), 0)
                quote = MarketQuote(
                    probability=raw_probability,
                    raw_probability=raw_probability,
                    normalized=False,
                    normalization_total=None,
                    market_id=str(market.get("id") or ""),
                    event_id=event_id,
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

    selected_by_event: dict[str, int] = {}
    for quote in quotes.values():
        selected_by_event[quote.event_id] = selected_by_event.get(quote.event_id, 0) + 1

    normalized_events: set[str] = set()
    for event_id, selected_count in selected_by_event.items():
        total = event_totals.get(event_id, 0.0)
        market_count = event_market_counts.get(event_id, 0)
        needed = max(5, int(math.ceil(market_count * 0.70)))
        if selected_count >= needed and 0.50 <= total <= 2.00:
            normalized_events.add(event_id)

    for team_name, quote in list(quotes.items()):
        if quote.event_id not in normalized_events:
            continue
        total = event_totals[quote.event_id]
        quotes[team_name] = replace(
            quote,
            probability=quote.raw_probability / total,
            normalized=True,
            normalization_total=total,
        )

    normalization_details = []
    for event_id in sorted(normalized_events):
        event = events_by_id[event_id]
        normalization_details.append(
            {
                "event_id": event_id,
                "event_slug": str(event.get("slug") or ""),
                "raw_yes_total": round(event_totals[event_id], 6),
                "active_contracts": event_market_counts[event_id],
                "matched_teams": selected_by_event.get(event_id, 0),
            }
        )

    metadata = {
        "source": "Polymarket",
        "market_type": "season_winner",
        "queries": list(queries),
        "requested_event_slug": event_slug,
        "direct_event_slug_used": direct_slug_used,
        "events_checked": len(events_by_id),
        "quotes_found": len(quotes),
        "normalization_applied": bool(normalized_events),
        "normalization": normalization_details,
        "errors": errors,
        "updated_at": utc_now_iso(),
    }
    return quotes, metadata


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
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


def _team_aliases(team: dict[str, Any]) -> tuple[str, ...]:
    name = str(team.get("name") or "").strip()
    short = str(team.get("short") or "").strip()
    key = normalize_name(name)
    aliases: list[str] = [name]
    aliases.extend(MARKET_ALIASES.get(key, ()))
    if short and len(short) >= 3:
        aliases.append(short)

    # Conservative generic variants. These are only used after the candidate
    # event has also matched the opposing club, which limits false positives.
    generic = re.sub(r"\b(FC|CF|SC|AFC)\b", "", name, flags=re.IGNORECASE).strip()
    if generic and generic != name:
        aliases.append(generic)
    if name.endswith(" United") and len(name.split()) > 2:
        aliases.append(name[: -len(" United")])
    if name.endswith(" City") and len(name.split()) > 2:
        aliases.append(name[: -len(" City")])

    unique: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        normalized = normalize_name(alias)
        if len(normalized) < 3 or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(alias)
    return tuple(unique)


def _alias_match_score(text: str, aliases: tuple[str, ...]) -> int:
    normalized_text = normalize_name(text)
    best = 0
    for alias in aliases:
        normalized_alias = normalize_name(alias)
        if not normalized_alias:
            continue
        if normalized_alias in normalized_text:
            best = max(best, 100 + len(normalized_alias))
    return best


def _event_start(event: dict[str, Any]) -> datetime | None:
    # Sports-specific kickoff fields are more reliable than generic startDate,
    # which can represent when an event/market became available for trading.
    for market in event.get("markets") or []:
        for key in ("gameStartTime", "eventStartTime"):
            parsed = _parse_datetime(market.get(key))
            if parsed:
                return parsed
    for key in ("gameStartTime", "eventStartTime"):
        parsed = _parse_datetime(event.get(key))
        if parsed:
            return parsed
    for market in event.get("markets") or []:
        for key in ("startDate", "startDateIso", "endDate", "endDateIso"):
            parsed = _parse_datetime(market.get(key))
            if parsed:
                return parsed
    for key in ("startDate", "startDateIso"):
        parsed = _parse_datetime(event.get(key))
        if parsed:
            return parsed
    return None


def _event_candidate_score(
    event: dict[str, Any],
    home_aliases: tuple[str, ...],
    away_aliases: tuple[str, ...],
    kickoff: datetime | None,
    league_terms: tuple[str, ...],
) -> tuple[int, float] | None:
    title = " ".join(str(event.get(key) or "") for key in ("title", "subtitle", "slug"))
    home_score = _alias_match_score(title, home_aliases)
    away_score = _alias_match_score(title, away_aliases)
    if home_score == 0 or away_score == 0:
        return None

    normalized_title = normalize_name(title)
    score = home_score + away_score
    if "vs" in str(event.get("title") or "").lower() or "versus" in str(event.get("title") or "").lower():
        score += 40
    if any(normalize_name(term) in normalized_title for term in league_terms if term):
        score += 30

    event_start = _event_start(event)
    date_distance_hours = 9999.0
    if kickoff and event_start:
        date_distance_hours = abs((event_start - kickoff).total_seconds()) / 3600
        if date_distance_hours > 36:
            return None
        score += max(0, int(36 - date_distance_hours))
    elif kickoff and not event_start:
        # A same-club search can surface old or unrelated events. Without a
        # verifiable event date, publishing no quote is safer than attaching
        # the wrong market to a fixture.
        return None

    liquidity = as_float(event.get("liquidity"), 0)
    volume = as_float(event.get("volume"), 0)
    quality = liquidity + volume * 0.001
    return score, quality


def _is_match_result_market(market: dict[str, Any]) -> bool:
    sports_type = normalize_name(str(market.get("sportsMarketType") or ""))
    text = normalize_name(_market_text(market))
    combined = f"{sports_type}{text}"
    if any(term in combined for term in EXCLUDED_MATCH_MARKET_TERMS):
        return False
    if sports_type:
        accepted_exact = {
            "moneyline",
            "ml",
            "1x2",
            "threeway",
            "threewaymoneyline",
            "matchresult",
            "fullgameml",
            "fulltimemoneyline",
            "fulltimeresult",
            "winner",
            "soccer1x2",
            "soccerwinner",
        }
        accepted_fragments = ("moneyline", "matchresult", "fulltime", "1x2", "threeway")
        if sports_type not in accepted_exact and not any(term in sports_type for term in accepted_fragments):
            return False
    return True


def _classify_label(
    label: str,
    home_aliases: tuple[str, ...],
    away_aliases: tuple[str, ...],
) -> str | None:
    normalized = normalize_name(label)
    if not normalized:
        return None
    if "draw" in normalized or normalized == "tie":
        return "draw"
    home_score = _alias_match_score(label, home_aliases)
    away_score = _alias_match_score(label, away_aliases)
    if home_score and not away_score:
        return "home"
    if away_score and not home_score:
        return "away"
    return None


def _question_subject(question: str) -> str:
    patterns = (
        r"\bif\s+(.+?)\s+wins?\b",
        r"\bwill\s+(.+?)\s+beat\b",
        r"\bwill\s+(.+?)\s+win\b",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ?.-")
    return ""


def _classify_binary_market(
    market: dict[str, Any],
    home_aliases: tuple[str, ...],
    away_aliases: tuple[str, ...],
) -> str | None:
    group_label = str(market.get("groupItemTitle") or "").strip()
    if group_label:
        classified = _classify_label(group_label, home_aliases, away_aliases)
        if classified:
            return classified

    question = str(market.get("question") or market.get("title") or "")
    if "draw" in normalize_name(question) or "endinatie" in normalize_name(question):
        return "draw"
    subject = _question_subject(question)
    if subject:
        return _classify_label(subject, home_aliases, away_aliases)

    # Slugs often preserve the decisive subject even when the question is terse.
    slug = str(market.get("slug") or "")
    if "draw" in normalize_name(slug):
        return "draw"
    return None


def _extract_match_outcomes(
    event: dict[str, Any],
    home_aliases: tuple[str, ...],
    away_aliases: tuple[str, ...],
) -> tuple[dict[str, float], dict[str, str], dict[str, str], float, float] | None:
    """Extract one coherent full-match 1X2 market set.

    A single three-way market is preferred. Otherwise the highest-quality
    binary Yes/No contract is selected independently for home, draw and away.
    Prices from derivative or incomplete markets are never mixed in.
    """
    three_way_candidates: list[
        tuple[float, dict[str, float], dict[str, str], dict[str, str], float, float]
    ] = []
    binary_candidates: dict[
        str, list[tuple[float, float, str, str, float, float]]
    ] = {"home": [], "draw": [], "away": []}

    for market in event.get("markets") or []:
        if market.get("active") is False or market.get("closed") is True:
            continue
        if not _is_match_result_market(market):
            continue

        market_id = str(market.get("id") or "")
        question = str(market.get("question") or market.get("groupItemTitle") or "")
        liquidity = as_float(market.get("liquidityNum") or market.get("liquidity"), 0)
        volume = as_float(market.get("volumeNum") or market.get("volume"), 0)
        quality = liquidity + volume * 0.001
        outcomes = [str(v) for v in _json_list(market.get("outcomes"))]
        prices = _json_list(market.get("outcomePrices"))
        lower_outcomes = [value.lower() for value in outcomes]

        if len(outcomes) >= 3 and len(outcomes) == len(prices) and "yes" not in lower_outcomes:
            candidate_raw: dict[str, float] = {}
            for label, price_value in zip(outcomes, prices):
                outcome = _classify_label(label, home_aliases, away_aliases)
                probability = as_float(price_value, -1)
                if outcome and 0 <= probability <= 1:
                    candidate_raw[outcome] = probability
            if set(candidate_raw) == {"home", "draw", "away"}:
                total = sum(candidate_raw.values())
                if 0.70 <= total <= 1.35:
                    ids = {outcome: market_id for outcome in candidate_raw}
                    questions = {outcome: question or outcome for outcome in candidate_raw}
                    three_way_candidates.append(
                        (quality, candidate_raw, ids, questions, liquidity, volume)
                    )
            continue

        outcome = _classify_binary_market(market, home_aliases, away_aliases)
        probability = _yes_probability(market)
        if not outcome or probability is None:
            continue
        binary_candidates[outcome].append(
            (quality, probability, market_id, question, liquidity, volume)
        )

    if three_way_candidates:
        _, raw, market_ids, questions, liquidity, volume = max(
            three_way_candidates, key=lambda item: item[0]
        )
        return raw, market_ids, questions, liquidity, volume

    if any(not binary_candidates[outcome] for outcome in ("home", "draw", "away")):
        return None

    raw: dict[str, float] = {}
    market_ids: dict[str, str] = {}
    questions: dict[str, str] = {}
    liquidity = 0.0
    volume = 0.0
    for outcome in ("home", "draw", "away"):
        _, probability, market_id, question, selected_liquidity, selected_volume = max(
            binary_candidates[outcome], key=lambda item: item[0]
        )
        raw[outcome] = probability
        market_ids[outcome] = market_id
        questions[outcome] = question
        liquidity += selected_liquidity
        volume += selected_volume

    total = sum(raw.values())
    if not 0.70 <= total <= 1.35:
        return None
    return raw, market_ids, questions, liquidity, volume


def _discover_active_soccer_events() -> tuple[list[dict[str, Any]], list[str], int]:
    """Fetch active soccer events systematically before using text search.

    Polymarket recommends the events endpoint for complete active-market
    discovery.  We keep public-search as a per-fixture fallback because search
    can still be useful for newly listed or unusually tagged games.
    """

    events_by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    pages = 0
    limit = 500
    for page_index in range(10):
        offset = page_index * limit
        try:
            response = requests.get(
                f"{GAMMA_URL}/events",
                params={
                    "tag_slug": "soccer",
                    "related_tags": True,
                    "active": True,
                    "closed": False,
                    "limit": limit,
                    "offset": offset,
                    "order": "start_date",
                    "ascending": False,
                },
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                page = payload
            elif isinstance(payload, dict):
                # Defensive compatibility with keyset/search-like wrappers and
                # with existing mocked tests.
                page = payload.get("events") or []
            else:
                page = []
        except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
            errors.append(f"event discovery: {exc}")
            break

        pages += 1
        valid_page = [event for event in page if isinstance(event, dict)]
        for event in valid_page:
            if event.get("active") is False or event.get("closed") is True:
                continue
            key = str(event.get("id") or event.get("slug") or "")
            if key:
                events_by_id[key] = event
        if len(valid_page) < limit:
            break

    return list(events_by_id.values()), errors, pages


def _search_match_event(
    query: str,
) -> tuple[list[dict[str, Any]], str | None]:
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
        payload = response.json()
        events = payload.get("events") if isinstance(payload, dict) else []
        return [event for event in (events or []) if isinstance(event, dict)], None
    except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
        return [], str(exc)


def fetch_match_quotes(
    fixtures: list[dict[str, Any]],
    teams: list[dict[str, Any]],
    league_terms: tuple[str, ...],
    lookahead_days: int = 21,
    max_fixtures: int = 60,
) -> tuple[dict[str, MatchMarketQuote], dict[str, Any]]:
    """Find exact 1X2 Polymarket events for near-term scheduled fixtures.

    Active soccer events are discovered systematically first.  Public text
    search is then used only when the broad event feed did not yield a valid
    exact market for a fixture.  Both paths still require both clubs, a
    compatible kickoff, and a complete home/draw/away result set.
    """

    team_by_slug = {str(team.get("slug")): team for team in teams}
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=max(1, lookahead_days))
    eligible: list[tuple[datetime, dict[str, Any]]] = []
    for fixture in fixtures:
        if fixture.get("status") == "final":
            continue
        kickoff = _parse_datetime(fixture.get("kickoff")) or _parse_datetime(fixture.get("date"))
        if kickoff is None:
            continue
        if kickoff < now - timedelta(hours=8) or kickoff > window_end:
            continue
        if fixture.get("home") not in team_by_slug or fixture.get("away") not in team_by_slug:
            continue
        eligible.append((kickoff, fixture))
    eligible.sort(key=lambda item: item[0])
    eligible = eligible[: max(1, max_fixtures)]

    quotes: dict[str, MatchMarketQuote] = {}
    discovery_events, discovery_errors, discovery_pages = _discover_active_soccer_events()
    errors: list[str] = list(discovery_errors)
    events_checked = 0
    fallback_searches = 0
    rejected_incomplete = 0
    rejected_ambiguous = 0

    def best_quote_for_events(
        fixture: dict[str, Any],
        kickoff: datetime,
        events: list[dict[str, Any]],
    ) -> MatchMarketQuote | None:
        nonlocal events_checked, rejected_incomplete, rejected_ambiguous
        home = team_by_slug[str(fixture["home"])]
        away = team_by_slug[str(fixture["away"])]
        home_aliases = _team_aliases(home)
        away_aliases = _team_aliases(away)
        candidates: list[tuple[int, float, MatchMarketQuote]] = []

        for event in events:
            events_checked += 1
            if event.get("active") is False or event.get("closed") is True:
                continue
            event_score = _event_candidate_score(event, home_aliases, away_aliases, kickoff, league_terms)
            if event_score is None:
                rejected_ambiguous += 1
                continue
            extracted = _extract_match_outcomes(event, home_aliases, away_aliases)
            if extracted is None:
                rejected_incomplete += 1
                continue
            raw, market_ids, questions, liquidity, volume = extracted
            total = raw["home"] + raw["draw"] + raw["away"]
            if total <= 0:
                rejected_incomplete += 1
                continue
            normalized = {key: value / total for key, value in raw.items()}
            event_slug = str(event.get("slug") or "")
            quote = MatchMarketQuote(
                fixture_id=str(fixture.get("id") or ""),
                home_probability=normalized["home"],
                draw_probability=normalized["draw"],
                away_probability=normalized["away"],
                home_raw_probability=raw["home"],
                draw_raw_probability=raw["draw"],
                away_raw_probability=raw["away"],
                normalized=abs(total - 1.0) > 0.0005,
                normalization_total=total,
                event_id=str(event.get("id") or ""),
                event_title=str(event.get("title") or ""),
                event_slug=event_slug,
                event_url=f"{POLYMARKET_EVENT_URL}/{event_slug}" if event_slug else "",
                kickoff=(_event_start(event) or kickoff).isoformat().replace("+00:00", "Z"),
                market_ids=market_ids,
                questions=questions,
                liquidity=liquidity or as_float(event.get("liquidity"), 0),
                volume=volume or as_float(event.get("volume"), 0),
                updated_at=utc_now_iso(),
            )
            score, quality = event_score
            candidates.append((score, quality + quote.liquidity + quote.volume * 0.001, quote))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    for kickoff, fixture in eligible:
        quote = best_quote_for_events(fixture, kickoff, discovery_events)
        if quote is None:
            home = team_by_slug[str(fixture["home"])]
            away = team_by_slug[str(fixture["away"])]
            query = f"{home['name']} vs {away['name']}"
            events, error = _search_match_event(query)
            fallback_searches += 1
            if error:
                errors.append(f"{fixture.get('id')}: {error}")
            else:
                quote = best_quote_for_events(fixture, kickoff, events)
        if quote is not None:
            quotes[str(fixture["id"])] = quote

    metadata = {
        "source": "Polymarket",
        "market_type": "match_1x2",
        "lookahead_days": lookahead_days,
        "max_fixtures": max_fixtures,
        "eligible_fixtures": len(eligible),
        "discovery_events": len(discovery_events),
        "discovery_pages": discovery_pages,
        "search_fallbacks": fallback_searches,
        "queries_sent": fallback_searches,
        "events_checked": events_checked,
        "quotes_found": len(quotes),
        "coverage": round(len(quotes) / len(eligible), 6) if eligible else 0.0,
        "rejected_incomplete_1x2": rejected_incomplete,
        "rejected_ambiguous": rejected_ambiguous,
        "errors": errors,
        "updated_at": utc_now_iso(),
        "note": (
            "Active soccer events are discovered through Polymarket's events endpoint first; "
            "public-search is only a fallback. Only exact three-outcome match-result markets "
            "are accepted, and market prices remain comparison-only."
        ),
    }
    return quotes, metadata
