"""LLM narrative and research layer.

Important boundary: the LLM never computes anything. Every number, level and
detection comes from the deterministic engines; the model's only job is to turn
that structured evidence into prose at the requested level of expertise. The
system prompt forbids inventing figures, and the caller passes the analysis in
as ground truth.

This matters because it is what keeps the platform's core promise — every
conclusion traceable to chart data — true even when the explanation is
generated. A model asked to "analyse this chart" from scratch would hallucinate
levels; a model asked to explain a computed report cannot.

The concept glossary works with no API key at all, so research mode degrades to
solid reference material rather than an error when no model is configured.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("legend.trading.narrative")

SYSTEM_PROMPT = (
    "You are the analysis voice of an institutional trading terminal. You are given a "
    "COMPLETE, already-computed market analysis as JSON. Your job is to explain it clearly.\n\n"
    "Hard rules:\n"
    "1. Never invent a number, price level, percentage or detection. Every figure you state "
    "must appear in the JSON you were given. If something is not in the data, say it is not "
    "available.\n"
    "2. Never give an unexplained directional call. Every conclusion must cite the specific "
    "evidence from the analysis that supports it.\n"
    "3. Describe the market context before discussing what might happen next. Context first, "
    "prediction second — always.\n"
    "4. State uncertainty honestly. If confidence is low or the sample size is small, lead with "
    "that rather than burying it.\n"
    "5. Never promise profits, and never present a setup as a sure thing. Mention invalidation "
    "as prominently as the target.\n"
    "6. Do not recommend leverage or position sizes beyond what the risk section states.\n"
)

AUDIENCE_STYLES = {
    "beginner": (
        "Write for someone new to trading. Define every term the first time you use it "
        "(order block, BOS, CHOCH, ATR). Use short sentences and plain words. Explain WHY each "
        "piece of evidence matters, not just what it is. Be encouraging about learning but "
        "blunt about risk."
    ),
    "intermediate": (
        "Write for a trader who knows the basics but not institutional concepts. Assume they "
        "know RSI and support/resistance; briefly explain smart-money terminology."
    ),
    "professional": (
        "Write for a professional trader. Use standard terminology without defining it. Be "
        "dense and specific. Lead with the thesis, then the evidence, then the invalidation. "
        "Skip anything obvious."
    ),
}


# ---------------------------------------------------------------------------
# Concept glossary — works with no API key.
# ---------------------------------------------------------------------------

CONCEPTS: dict[str, dict] = {
    "bos": {
        "title": "Break of Structure (BOS)",
        "short": "A close beyond the prior swing in the direction of the existing trend.",
        "beginner": (
            "Price moves in steps. In an uptrend each step makes a higher high and a higher low. "
            "A Break of Structure is when price closes above the most recent high in that "
            "staircase — the trend just took another step up, so it is continuing.\n\n"
            "The word 'closes' matters. If price only pokes above the level with a wick and then "
            "falls back, that is not a break; it is a liquidity sweep, which often means the "
            "opposite thing."
        ),
        "professional": (
            "BOS confirms trend continuation: a decisive close through the prior swing high in a "
            "bullish sequence (or swing low in a bearish one). Distinguish from CHOCH by "
            "reference to the prevailing structural direction — the same level break is a BOS "
            "with trend and a CHOCH against it.\n\n"
            "This platform requires a candle close beyond the level, not a wick, precisely so "
            "stop-runs are classified as sweeps rather than breaks."
        ),
    },
    "choch": {
        "title": "Change of Character (CHOCH)",
        "short": "The first close beyond a swing against the prevailing trend — the earliest structural warning of a reversal.",
        "beginner": (
            "If price has been making higher highs and higher lows, and then it closes BELOW the "
            "most recent higher low, the pattern just broke for the first time. That is a Change "
            "of Character.\n\n"
            "It does not guarantee a reversal. It means the buyers who had been in control just "
            "lost a level they had been defending, so the assumption that the uptrend continues "
            "is no longer free."
        ),
        "professional": (
            "CHOCH is the first counter-trend structural break, marking the transition from "
            "trend continuation to potential distribution/accumulation. Treat it as an alert to "
            "reduce trend exposure rather than a reversal entry on its own — confirmation "
            "typically comes from a subsequent failed retest of the broken level."
        ),
    },
    "order_block": {
        "title": "Order Block",
        "short": "The last opposing candle before a displacement move that breaks structure.",
        "beginner": (
            "When a large institution buys a lot, it cannot fill the whole order at once without "
            "moving the price against itself. The theory is that they leave part of the order "
            "behind, and when price returns to that area later, the rest gets filled — which is "
            "why price often bounces there.\n\n"
            "In practice we identify it as the last down-candle before a strong move up (for a "
            "bullish order block). The key requirement is that the move afterwards was genuinely "
            "strong — otherwise it is just an ordinary candle."
        ),
        "professional": (
            "Last opposing candle preceding a displacement leg that produces a BOS or CHOCH. "
            "This platform requires the subsequent move to exceed 1.5x ATR to qualify, which "
            "filters the noise that makes naive order-block detection useless. Zones are marked "
            "mitigated once price has traded back through them."
        ),
    },
    "fvg": {
        "title": "Fair Value Gap (FVG) / Imbalance",
        "short": "A three-bar pattern where the first and third bars do not overlap, leaving a price range that traded in only one direction.",
        "beginner": (
            "Normally, as price moves, buyers and sellers trade at every price on the way. "
            "Sometimes price moves so fast that a range gets skipped — the high of one candle is "
            "below the low of the candle two bars later.\n\n"
            "That skipped range is a Fair Value Gap. The idea is that the market tends to come "
            "back and trade through it later to 'fill' the gap, because that price was never "
            "properly auctioned."
        ),
        "professional": (
            "Three-candle imbalance: candle[i-1].high < candle[i+1].low for a bullish FVG. "
            "Represents inefficient delivery that price frequently rebalances. Used as an entry "
            "zone in the direction of the displacement, and as a target when unfilled."
        ),
    },
    "liquidity": {
        "title": "Liquidity",
        "short": "Clusters of resting stop orders — typically just beyond equal highs, equal lows, and obvious swing points.",
        "beginner": (
            "Traders put stop-losses in predictable places: just under a recent low if they are "
            "long, just above a recent high if they are short. Those stops are pending orders.\n\n"
            "A large player who wants to buy needs someone to sell to them. All those stop-losses "
            "below the low are sell orders waiting to trigger. So price often dips below an "
            "obvious low, triggers the stops, and then reverses sharply — that dip was the "
            "liquidity being taken.\n\n"
            "Where you would put your stop is where liquidity is."
        ),
        "professional": (
            "Buy-side liquidity rests above swing highs and equal highs; sell-side below swing "
            "lows and equal lows. Equal highs/lows concentrate it most heavily. Internal "
            "liquidity sits within the current dealing range, external beyond it. Draw on "
            "liquidity is the primary directional bias tool once structure is established."
        ),
    },
    "premium_discount": {
        "title": "Premium and Discount",
        "short": "Where price sits within its current dealing range: above 50% is premium (expensive), below is discount (cheap).",
        "beginner": (
            "Take the current trading range, from its low to its high, and find the midpoint. "
            "Above the midpoint, price is expensive — that is 'premium'. Below it, price is "
            "cheap — that is 'discount'.\n\n"
            "The simple rule: prefer buying in discount and selling in premium. Buying at the "
            "top of a range is how people end up with a terrible entry and a huge stop."
        ),
        "professional": (
            "Equilibrium at the 50% of the dealing range. Institutional buying is expected in "
            "discount, selling in premium. Combined with OTE (62-79% retracement) this frames "
            "entry quality independently of the directional thesis."
        ),
    },
    "ote": {
        "title": "Optimal Trade Entry (OTE)",
        "short": "The 62-79% retracement of an impulse leg, where trend pullbacks most often end.",
        "beginner": (
            "After a strong move up, price usually pulls back before continuing. The OTE is the "
            "zone where that pullback most often ends — between 62% and 79% of the way back down "
            "the move.\n\n"
            "Entering there rather than chasing the breakout gives you a much smaller stop for "
            "the same target, which is what makes the reward-to-risk work."
        ),
        "professional": (
            "62-79% retracement band, encompassing the 0.705 equilibrium and 0.786 levels. Used "
            "in the direction of the last structural break. Its value is stop compression: same "
            "target, materially tighter invalidation."
        ),
    },
    "wyckoff": {
        "title": "Wyckoff Method",
        "short": "A framework describing how large operators accumulate and distribute positions in four phases.",
        "beginner": (
            "Richard Wyckoff's idea is that big money cannot buy or sell in one go without "
            "wrecking their own price. So they work in phases:\n\n"
            "1. Accumulation — quietly buying in a range after a downtrend, while it looks boring.\n"
            "2. Markup — the trend up, once they have their position.\n"
            "3. Distribution — quietly selling into strength in a range, while retail is excited.\n"
            "4. Markdown — the trend down.\n\n"
            "The practical takeaway: long, dull ranges after a big move are usually someone "
            "building or unloading a position, not the market doing nothing."
        ),
        "professional": (
            "Four-phase cycle: accumulation, markup, distribution, markdown. Key events include "
            "the selling climax, automatic rally, secondary test, spring (a false break below "
            "support that traps sellers), and sign of strength. Distribution mirrors with the "
            "upthrust. This platform infers accumulation/distribution from volatility "
            "compression combined with position in the range — it does not attempt to label "
            "individual Wyckoff events, which requires more context than OHLCV provides."
        ),
    },
    "ict": {
        "title": "ICT (Inner Circle Trader) Concepts",
        "short": "A methodology built around liquidity, imbalance, and institutional order flow.",
        "beginner": (
            "ICT is a trading methodology that focuses on three ideas:\n\n"
            "1. The market moves from one pool of liquidity to another — it hunts stops.\n"
            "2. Fast moves leave imbalances (fair value gaps) that get revisited.\n"
            "3. There are specific times of day when the big moves start.\n\n"
            "The core sequence to look for: liquidity gets taken, structure changes, price "
            "retraces into an order block or gap, then continues in the new direction."
        ),
        "professional": (
            "Core model: liquidity sweep -> displacement -> CHOCH/BOS -> retracement into the "
            "originating order block or FVG -> continuation toward the opposing liquidity pool. "
            "Overlays premium/discount and OTE for entry grading. Killzone timing (London open, "
            "New York open) narrows the window; this platform exposes session filters for that "
            "but does not assume killzone edge without the user enabling it."
        ),
    },
    "smc": {
        "title": "Smart Money Concepts (SMC)",
        "short": "An umbrella term for reading charts through institutional order flow: structure, liquidity, order blocks and imbalance.",
        "beginner": (
            "Smart Money Concepts is a way of reading charts that assumes large institutions "
            "move the market, and that their footprints are visible.\n\n"
            "Instead of asking 'is RSI oversold?', it asks 'where are the stop-losses, and which "
            "side is the market likely to go take?'\n\n"
            "The main tools are market structure (BOS and CHOCH), liquidity pools, order blocks, "
            "and fair value gaps."
        ),
        "professional": (
            "SMC synthesises structure (BOS/CHOCH), liquidity mapping, POI identification (order "
            "blocks, breakers, mitigation blocks) and imbalance. Its practical strength is "
            "framing entries around invalidation rather than around indicator thresholds; its "
            "weakness is that most of its definitions are discretionary, which is why this "
            "platform states the specific rule it applies for every detection."
        ),
    },
    "liquidity_sweep": {
        "title": "Liquidity Sweep / Stop Hunt",
        "short": "A wick through an obvious level that the candle body fails to hold — stops triggered, price rejected.",
        "beginner": (
            "Price pushes below an obvious low, triggers everyone's stop-losses, and then closes "
            "back above that low. The 'breakdown' lasted only minutes.\n\n"
            "That is a sweep. It usually means someone needed those sell orders in order to buy, "
            "and price is now more likely to go up than down — the opposite of what the "
            "breakdown appeared to signal."
        ),
        "professional": (
            "Wick through a prior swing with a close back inside. Distinguished from a BOS purely "
            "by the close. Frequently precedes displacement in the opposite direction, and when "
            "it occurs shortly before a structural break it is classified as inducement."
        ),
    },
    "inducement": {
        "title": "Inducement",
        "short": "The obvious liquidity taken first, before the real move begins.",
        "beginner": (
            "Before a big move, the market often takes out the most obvious level first — the one "
            "everyone was watching. That traps traders on the wrong side and gives the real move "
            "the orders it needs.\n\n"
            "So a sweep of an obvious high, immediately followed by a break down, is not two "
            "contradictory signals. The sweep was setting up the break."
        ),
        "professional": (
            "Minor liquidity grab preceding the genuine displacement leg, in the opposite "
            "direction to the eventual move. This platform detects it as a sweep occurring within "
            "15 bars before a structural event of opposing direction."
        ),
    },
    "rsi_divergence": {
        "title": "RSI Divergence",
        "short": "Price makes a new extreme but RSI does not — momentum is not confirming the move.",
        "beginner": (
            "RSI measures momentum. Normally, when price makes a new high, RSI does too.\n\n"
            "Divergence is when price makes a higher high but RSI makes a LOWER high. The new "
            "price high was achieved with less force than the last one — buyers are getting "
            "tired.\n\n"
            "Important caveat: divergence can persist for a long time in a strong trend. It is a "
            "warning to tighten risk, not a signal to short."
        ),
        "professional": (
            "Regular divergence signals potential exhaustion; hidden divergence signals "
            "continuation. In trending regimes divergence is a poor standalone reversal signal — "
            "it is best used to grade the quality of a counter-trend setup that already has "
            "structural confirmation."
        ),
    },
}


def explain_concept(concept: str, audience: str = "intermediate") -> dict:
    """Look up a trading concept. Works without any API key."""
    key = concept.lower().strip().replace(" ", "_").replace("-", "_")

    aliases = {
        "break_of_structure": "bos", "change_of_character": "choch",
        "fair_value_gap": "fvg", "imbalance": "fvg", "gap": "fvg",
        "orderblock": "order_block", "ob": "order_block",
        "premium": "premium_discount", "discount": "premium_discount",
        "optimal_trade_entry": "ote",
        "smart_money": "smc", "smart_money_concepts": "smc",
        "stop_hunt": "liquidity_sweep", "sweep": "liquidity_sweep",
        "divergence": "rsi_divergence",
        "buy_side_liquidity": "liquidity", "sell_side_liquidity": "liquidity",
        "inner_circle_trader": "ict",
    }
    key = aliases.get(key, key)

    entry = CONCEPTS.get(key)
    if not entry:
        return {
            "found": False,
            "concept": concept,
            "available": sorted(CONCEPTS.keys()),
            "message": (
                f"No glossary entry for '{concept}'. Available concepts: "
                f"{', '.join(sorted(CONCEPTS.keys()))}. "
                f"For chart-specific questions, use the research endpoint with a symbol attached."
            ),
        }

    level = "beginner" if audience == "beginner" else "professional"
    return {
        "found": True,
        "concept": key,
        "title": entry["title"],
        "short": entry["short"],
        "explanation": entry[level],
        "audience": audience,
    }


# ---------------------------------------------------------------------------
# LLM layer
# ---------------------------------------------------------------------------

def _select_provider(preferred: str | None = None):
    """Reuse the app's pluggable provider selection when it is available."""
    try:
        from ai.providers import select_provider

        return select_provider(preferred)
    except Exception:  # noqa: BLE001 - narrative is optional; never break analysis
        return None


def _call_llm(system: str, user: str, preferred: str | None = None) -> str | None:
    """Single-shot completion with no tools. Returns None when no model is configured."""
    provider = _select_provider(preferred)
    if provider is None:
        return None

    try:
        if provider.name == "anthropic":
            import anthropic
            from app.config import settings

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "".join(b.text for b in response.content if b.type == "text")

        if provider.name == "gemini":
            import httpx
            from app.config import settings

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
            )
            body = {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
            }
            response = httpx.post(url, json=body, timeout=90)
            response.raise_for_status()
            candidates = response.json().get("candidates", [])
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
    except Exception as exc:  # noqa: BLE001 - degrade to deterministic output
        logger.warning("narrative LLM call failed: %s", exc)
        return None

    return None


def narrate_analysis(analysis: dict, audience: str = "intermediate", model: str | None = None) -> dict:
    """Turn a computed analysis into prose at the requested expertise level.

    Falls back to the deterministic executive summary when no model is
    configured — the platform stays fully usable without an LLM key.
    """
    style = AUDIENCE_STYLES.get(audience, AUDIENCE_STYLES["intermediate"])

    # Strip the chart payload: it is large and adds nothing to an explanation.
    payload = {k: v for k, v in analysis.items() if k != "chart_data"}

    prompt = (
        f"{style}\n\n"
        f"Here is the complete computed analysis. Explain it in the order the platform "
        f"produces it: market context first, then structure, then the evidence, then the "
        f"probabilities, then the setup and its risks. Cite the specific numbers from this "
        f"data.\n\n"
        f"```json\n{json.dumps(payload, indent=2)[:60_000]}\n```"
    )

    text = _call_llm(SYSTEM_PROMPT, prompt, model)

    if text is None:
        return {
            "available": False,
            "audience": audience,
            "narrative": analysis.get("executive_summary", ""),
            "note": (
                "No language model is configured, so this is the deterministic summary. "
                "Every section of the analysis is still fully computed and explained — set "
                "ANTHROPIC_API_KEY or GEMINI_API_KEY to get the prose walkthrough as well."
            ),
        }

    return {
        "available": True,
        "audience": audience,
        "narrative": text,
        "note": (
            "Explanation generated from the computed analysis. All figures come from the "
            "deterministic engines; the model only rephrases them."
        ),
    }


def research(question: str, analysis: dict | None = None, audience: str = "intermediate",
             model: str | None = None) -> dict:
    """Answer a trading question, optionally grounded in a live analysis.

    Concept questions are answered from the built-in glossary first, so they
    work with no API key and give the same (correct) answer every time.
    """
    lowered = question.lower()

    # Try the glossary for definitional questions before reaching for a model.
    if any(word in lowered for word in ("what is", "what's", "explain", "define", "meaning of")):
        for key, entry in CONCEPTS.items():
            tokens = [key.replace("_", " "), entry["title"].lower()]
            if any(token in lowered for token in tokens if len(token) > 2):
                result = explain_concept(key, audience)
                if analysis is None:
                    result["source"] = "glossary"
                    return result
                break

    if analysis is not None:
        payload = {k: v for k, v in analysis.items() if k != "chart_data"}
        prompt = (
            f"{AUDIENCE_STYLES.get(audience, AUDIENCE_STYLES['intermediate'])}\n\n"
            f"The trader asked: \"{question}\"\n\n"
            f"Answer using ONLY the analysis below. Quote the specific levels, detections and "
            f"figures that support your answer. If the analysis does not contain what is needed "
            f"to answer, say exactly that rather than guessing.\n\n"
            f"```json\n{json.dumps(payload, indent=2)[:60_000]}\n```"
        )
    else:
        prompt = (
            f"{AUDIENCE_STYLES.get(audience, AUDIENCE_STYLES['intermediate'])}\n\n"
            f"Answer this trading question: \"{question}\"\n\n"
            f"You have no chart data, so answer conceptually and say clearly that you are not "
            f"looking at a specific chart. Do not invent price levels."
        )

    text = _call_llm(SYSTEM_PROMPT, prompt, model)

    if text is None:
        glossary = explain_concept(question, audience)
        if glossary.get("found"):
            glossary["source"] = "glossary"
            glossary["note"] = "Answered from the built-in glossary; no language model is configured."
            return glossary
        return {
            "available": False,
            "question": question,
            "answer": "",
            "note": (
                "No language model is configured, and this question is not in the built-in "
                "glossary. Set ANTHROPIC_API_KEY or GEMINI_API_KEY to enable free-form research, "
                f"or ask about one of: {', '.join(sorted(CONCEPTS.keys()))}."
            ),
        }

    return {
        "available": True,
        "question": question,
        "answer": text,
        "grounded": analysis is not None,
        "audience": audience,
        "source": "model",
    }


def propose_strategy(description: str, model: str | None = None) -> dict | None:
    """Ask the model to express a description as a StrategySpec dict.

    The deterministic parser in `strategy/nl.py` handles this without a model;
    this path exists for descriptions the vocabulary does not cover. The result
    is validated by `StrategySpec.from_dict` before use, so a malformed or
    hallucinated response is rejected rather than silently backtested.
    """
    schema_hint = {
        "name": "string",
        "entry_long": [{"left": "ema(20)", "op": "crosses_above", "right": "ema(50)"}],
        "entry_short": [{"left": "ema(20)", "op": "crosses_below", "right": "ema(50)"}],
        "filters": [{"left": "adx(14)", "op": ">", "right": 25}],
        "stop": {"type": "atr", "value": 1.5},
        "targets": {"type": "rr", "values": [1.5, 3.0]},
        "risk_percent": 1.0,
    }
    system = (
        "You convert trading strategy descriptions into a strict JSON specification. "
        "Reply with JSON only — no prose, no markdown fences.\n\n"
        "Valid operators: >, <, >=, <=, crosses_above, crosses_below, rising, falling.\n"
        "Valid features: close, open, high, low, volume, hl2, hlc3, ema(n), sma(n), rsi(n), "
        "atr(n), macd, macd_signal, macd_hist, adx(n), plus_di, minus_di, bb_upper(n), "
        "bb_middle(n), bb_lower(n), stoch_k, stoch_d, supertrend, supertrend_dir, highest(n), "
        "lowest(n), vwap, volume_sma(n), volume_ratio(n), cci(n), mfi(n), obv, body, range, "
        "body_ratio.\n"
        "Use ONLY these features. Never invent one."
    )
    prompt = (
        f"Strategy description: \"{description}\"\n\n"
        f"Return JSON matching this shape:\n{json.dumps(schema_hint, indent=2)}"
    )

    text = _call_llm(system, prompt, model)
    if not text:
        return None

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        logger.warning("model returned unparseable strategy JSON")
        return None
