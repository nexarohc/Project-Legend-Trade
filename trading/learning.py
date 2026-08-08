"""Learning engine.

"Learning" here means calibration, not model training. Every analysis is stored
with the call it made, and once enough bars have printed the engine goes back,
fetches what price actually did, and scores it. Over time that produces an
honest hit rate per scenario, per symbol and per confidence band — which is the
only way to know whether a "72% bullish continuation" has ever meant anything.

The same store keeps backtest results and user feedback, so the platform can
say things like "setups you rated useful were the ones where HTF bias agreed"
rather than treating every past analysis as equally informative.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from database.models import (
    AnalysisFeedback,
    AnalysisRecord,
    BacktestRecord,
    StrategyRecord,
)
from trading.models import Timeframe

logger = logging.getLogger("legend.trading.learning")

# How many bars forward we wait before judging an analysis. Matches the
# probability engine's default horizon so the score answers the same question
# the prediction asked.
DEFAULT_HORIZON = 10


def record_analysis(session: Session, analysis: dict, user_id: int = 1) -> int:
    """Persist an analysis so it can be scored later. Returns the record id."""
    setup = analysis.get("trade_setup") or {}
    probs = analysis.get("probability_analysis") or {}
    context = analysis.get("market_context") or {}
    verdict = analysis.get("verdict") or {}

    dominant_name, dominant_value = _dominant(probs)

    record = AnalysisRecord(
        user_id=user_id,
        symbol=analysis["symbol"],
        timeframe=analysis["timeframe"],
        provider=analysis.get("provider", ""),
        price_at_analysis=analysis["price"],
        trend=context.get("trend", ""),
        regime=context.get("regime", ""),
        dominant_scenario=dominant_name,
        dominant_probability=dominant_value,
        confidence=probs.get("confidence", 0.0),
        verdict=verdict.get("decision", ""),
        setup_direction=setup.get("direction") if setup.get("valid") else None,
        setup_entry=setup.get("entry") if setup.get("valid") else None,
        setup_stop=setup.get("stop_loss") if setup.get("valid") else None,
        setup_target=(setup.get("take_profits") or [None])[0] if setup.get("valid") else None,
        # Store a trimmed payload: chart data is large and reproducible.
        payload_json=json.dumps({
            k: v for k, v in analysis.items() if k != "chart_data"
        })[:200_000],
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record.id


def record_backtest(session: Session, study: dict, strategy_id: int | None = None,
                    user_id: int = 1) -> int:
    """Persist a completed strategy study."""
    metrics = study.get("metrics") or {}
    audit = study.get("audit") or {}
    backtest = study.get("backtest") or {}

    record = BacktestRecord(
        user_id=user_id,
        strategy_id=strategy_id,
        strategy_name=backtest.get("spec_name", ""),
        symbol=backtest.get("symbol", ""),
        timeframe=backtest.get("timeframe", ""),
        trade_count=metrics.get("total_trades", 0),
        win_rate=metrics.get("win_rate", 0.0),
        profit_factor=metrics.get("profit_factor"),
        net_profit_percent=metrics.get("net_profit_percent", 0.0),
        max_drawdown_percent=metrics.get("max_drawdown_percent", 0.0),
        sharpe_ratio=metrics.get("sharpe_ratio"),
        verdict=audit.get("verdict"),
        audit_score=audit.get("score"),
        metrics_json=json.dumps(metrics),
    )
    session.add(record)

    if strategy_id:
        strategy = session.get(StrategyRecord, strategy_id)
        # Never let one account's backtest overwrite another's strategy record.
        if strategy and strategy.user_id == user_id:
            strategy.last_verdict = audit.get("verdict")
            strategy.last_score = audit.get("score")
            strategy.last_net_profit_percent = metrics.get("net_profit_percent")
            strategy.last_trade_count = metrics.get("total_trades")
            strategy.last_tested_at = dt.datetime.utcnow()

    session.commit()
    session.refresh(record)
    return record.id


def record_feedback(session: Session, analysis_id: int | None, rating: str, comment: str = "",
                    user_id: int = 1) -> int:
    # Only accept feedback on an analysis the caller actually owns.
    if analysis_id is not None:
        target = session.get(AnalysisRecord, analysis_id)
        if target is None or target.user_id != user_id:
            analysis_id = None
    record = AnalysisFeedback(
        user_id=user_id, analysis_id=analysis_id, rating=rating, comment=comment
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record.id


def score_pending_analyses(session: Session, horizon: int = DEFAULT_HORIZON, limit: int = 50,
                           user_id: int | None = None) -> dict:
    """Fetch what actually happened after each unscored analysis and grade it.

    Only analyses old enough for `horizon` bars to have printed are considered;
    grading one before its horizon has elapsed would be scoring a prediction
    that has not resolved yet.
    """
    from trading.market import market_service

    query = session.query(AnalysisRecord).filter(AnalysisRecord.scored_at.is_(None))
    if user_id is not None:
        query = query.filter(AnalysisRecord.user_id == user_id)
    pending = (
        query
        .order_by(AnalysisRecord.created_at.asc())
        .limit(limit)
        .all()
    )

    scored = 0
    skipped = 0
    errors: list[str] = []

    for record in pending:
        try:
            timeframe = Timeframe.parse(record.timeframe)
        except ValueError:
            skipped += 1
            continue

        age_seconds = (dt.datetime.utcnow() - record.created_at).total_seconds()
        if age_seconds < timeframe.seconds * horizon:
            skipped += 1
            continue

        try:
            series = market_service.candles(record.symbol, timeframe, horizon + 5, record.provider or None)
        except Exception as exc:  # noqa: BLE001 - a dead symbol shouldn't stop the batch
            errors.append(f"{record.symbol}: {exc}")
            continue

        if not series.candles:
            skipped += 1
            continue

        outcome_price = series.last.close
        change = (outcome_price - record.price_at_analysis) / record.price_at_analysis * 100

        # Classify against the same thresholds the probability engine uses:
        # under roughly a percent of movement is "went nowhere".
        if abs(change) < 1.0:
            outcome = "range"
        elif change > 0:
            outcome = "up"
        else:
            outcome = "down"

        record.outcome = outcome
        record.outcome_price = outcome_price
        record.outcome_return = change
        record.was_correct = _was_correct(record, outcome)
        record.scored_at = dt.datetime.utcnow()
        scored += 1

    session.commit()
    return {"scored": scored, "skipped": skipped, "errors": errors, "pending_examined": len(pending)}


def _was_correct(record: AnalysisRecord, outcome: str) -> bool | None:
    """Did the analysis's dominant call match what happened?

    Returns None when the call has no directional content to grade (a range
    call scored against a range outcome is correct; a "no trade" verdict is
    not a directional prediction at all).
    """
    scenario = (record.dominant_scenario or "").lower()

    if "range" in scenario or "false breakout" in scenario:
        return outcome == "range"
    if "bullish" in scenario:
        return outcome == "up"
    if "bearish" in scenario:
        return outcome == "down"
    if "reversal" in scenario:
        trend = (record.trend or "").lower()
        if trend == "bullish":
            return outcome == "down"
        if trend == "bearish":
            return outcome == "up"
        return None
    if "breakout" in scenario:
        return outcome in ("up", "down")
    return None


def calibration_report(session: Session, symbol: str | None = None,
                       user_id: int | None = None) -> dict:
    """How well have past calls actually held up?

    This is the report that keeps the probability engine honest: if analyses
    labelled 70%+ confidence are right 45% of the time, the confidence scoring
    is miscalibrated and the report says so.
    """
    query = session.query(AnalysisRecord).filter(AnalysisRecord.scored_at.isnot(None))
    if user_id is not None:
        query = query.filter(AnalysisRecord.user_id == user_id)
    if symbol:
        query = query.filter(AnalysisRecord.symbol == symbol.upper())
    records = query.all()

    graded = [r for r in records if r.was_correct is not None]
    if not graded:
        return {
            "available": False,
            "reason": (
                "No analyses have been scored yet. Calls are graded automatically once "
                f"{DEFAULT_HORIZON} bars have printed since the analysis was made."
            ),
            "total_recorded": len(records),
        }

    correct = sum(1 for r in graded if r.was_correct)
    overall = 100 * correct / len(graded)

    by_scenario: dict[str, dict] = {}
    for record in graded:
        bucket = by_scenario.setdefault(
            record.dominant_scenario or "unknown", {"total": 0, "correct": 0}
        )
        bucket["total"] += 1
        bucket["correct"] += 1 if record.was_correct else 0
    for stats in by_scenario.values():
        stats["hit_rate"] = round(100 * stats["correct"] / stats["total"], 1)

    # Confidence calibration: does a higher stated confidence actually win more?
    bands = {"0-40": [], "40-60": [], "60-80": [], "80-100": []}
    for record in graded:
        c = record.confidence
        key = "0-40" if c < 40 else "40-60" if c < 60 else "60-80" if c < 80 else "80-100"
        bands[key].append(record.was_correct)

    calibration = {}
    for band, results in bands.items():
        if results:
            calibration[band] = {
                "count": len(results),
                "hit_rate": round(100 * sum(1 for r in results if r) / len(results), 1),
            }

    findings = [
        f"{correct} of {len(graded)} graded calls were correct ({overall:.1f}%)."
    ]
    ordered = sorted(by_scenario.items(), key=lambda kv: -kv[1]["total"])
    for scenario, stats in ordered[:5]:
        findings.append(
            f"'{scenario}' calls: {stats['hit_rate']}% correct over {stats['total']} samples."
        )

    high = calibration.get("80-100") or calibration.get("60-80")
    low = calibration.get("0-40")
    if high and low and high["count"] >= 5 and low["count"] >= 5:
        if high["hit_rate"] <= low["hit_rate"]:
            findings.append(
                "High-confidence calls are not outperforming low-confidence ones. The confidence "
                "score is not currently calibrated on this data — treat it as a rough indicator only."
            )
        else:
            findings.append(
                f"Confidence is behaving sensibly: high-confidence calls hit "
                f"{high['hit_rate']}% against {low['hit_rate']}% for low-confidence ones."
            )

    if len(graded) < 30:
        findings.append(
            f"Only {len(graded)} graded calls so far — too few to draw firm conclusions. "
            f"Accuracy figures will stabilise as more analyses are scored."
        )

    return {
        "available": True,
        "symbol": symbol,
        "total_recorded": len(records),
        "total_graded": len(graded),
        "overall_hit_rate": round(overall, 1),
        "by_scenario": by_scenario,
        "confidence_calibration": calibration,
        "findings": findings,
    }


def strategy_leaderboard(session: Session, limit: int = 20, user_id: int | None = None) -> list[dict]:
    """Best-performing audited strategies, so successful patterns are reusable."""
    query = session.query(BacktestRecord)
    if user_id is not None:
        query = query.filter(BacktestRecord.user_id == user_id)
    records = (
        query
        .order_by(BacktestRecord.audit_score.desc().nullslast(),
                  BacktestRecord.net_profit_percent.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "strategy_name": r.strategy_name,
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "trades": r.trade_count,
            "win_rate": round(r.win_rate, 1),
            "profit_factor": round(r.profit_factor, 2) if r.profit_factor else None,
            "net_profit_percent": round(r.net_profit_percent, 2),
            "max_drawdown_percent": round(r.max_drawdown_percent, 2),
            "sharpe_ratio": round(r.sharpe_ratio, 2) if r.sharpe_ratio else None,
            "verdict": r.verdict,
            "audit_score": round(r.audit_score, 1) if r.audit_score else None,
            "tested_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


def lessons_learned(session: Session, user_id: int | None = None) -> dict:
    """Patterns across everything stored — what has and has not worked here.

    Every claim comes with its sample size, because a "100% win rate" over
    three analyses is not a lesson.
    """
    analysis_query = session.query(AnalysisRecord).filter(AnalysisRecord.was_correct.isnot(None))
    backtest_query = session.query(BacktestRecord)
    feedback_query = session.query(AnalysisFeedback)
    if user_id is not None:
        analysis_query = analysis_query.filter(AnalysisRecord.user_id == user_id)
        backtest_query = backtest_query.filter(BacktestRecord.user_id == user_id)
        feedback_query = feedback_query.filter(AnalysisFeedback.user_id == user_id)

    analyses = analysis_query.all()
    backtests = backtest_query.all()
    feedback = feedback_query.all()

    lessons: list[str] = []

    if analyses:
        # Which regimes produce reliable calls?
        by_regime: dict[str, list[bool]] = {}
        for record in analyses:
            by_regime.setdefault(record.regime or "unknown", []).append(bool(record.was_correct))
        for regime, results in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
            if len(results) >= 5:
                rate = 100 * sum(results) / len(results)
                lessons.append(
                    f"In '{regime}' conditions, calls have been {rate:.0f}% accurate "
                    f"across {len(results)} samples."
                )

    if backtests:
        approved = [b for b in backtests if b.verdict == "APPROVED"]
        rejected = [b for b in backtests if b.verdict == "REJECT"]
        lessons.append(
            f"{len(backtests)} strategies tested: {len(approved)} approved, {len(rejected)} rejected."
        )
        if approved:
            avg_trades = sum(b.trade_count for b in approved) / len(approved)
            lessons.append(
                f"Approved strategies averaged {avg_trades:.0f} trades — sample size is the "
                f"single most common reason a strategy fails the audit."
            )

    if feedback:
        useful = sum(1 for f in feedback if f.rating == "useful")
        lessons.append(
            f"You have rated {len(feedback)} analyses, {useful} of them useful "
            f"({100 * useful / len(feedback):.0f}%)."
        )

    if not lessons:
        lessons.append(
            "Nothing has been recorded yet. Run analyses and backtests, and this section will "
            "fill with measured results rather than assertions."
        )

    return {
        "analyses_graded": len(analyses),
        "backtests_run": len(backtests),
        "feedback_entries": len(feedback),
        "lessons": lessons,
    }


def _dominant(probabilities: dict) -> tuple[str, float]:
    options = {
        "bullish continuation": probabilities.get("bullish_continuation", 0.0),
        "bearish continuation": probabilities.get("bearish_continuation", 0.0),
        "breakout": probabilities.get("breakout", 0.0),
        "reversal": probabilities.get("reversal", 0.0),
        "range": probabilities.get("range_bound", 0.0),
        "false breakout": probabilities.get("false_breakout", 0.0),
    }
    if not any(options.values()):
        return "", 0.0
    name = max(options, key=options.get)
    return name, options[name]
