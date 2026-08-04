"""Market scanner API: filter a caller-supplied symbol list by measurable conditions.

There is no bundled ticker list here, matching the rest of the platform — a
scan runs over symbols the caller supplies (a watchlist, anything), not "every
NASDAQ stock", since no keyless provider exposes a screener API.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.ratelimit import compute_limiter
from trading.models import MarketDataError, Timeframe
from trading.scanner import MAX_SCAN_SYMBOLS, Op, ScanField, ScanFilter, run_scan

router = APIRouter(prefix="/scanner", tags=["scanner"])


class ScanFilterRequest(BaseModel):
    field: str
    op: str
    value: float | str
    value2: float | None = None


class ScanRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=MAX_SCAN_SYMBOLS)
    timeframe: str = "1d"
    filters: list[ScanFilterRequest] = Field(default_factory=list)
    bars: int = Field(200, ge=30, le=1000)
    provider: str | None = None


def _parse_filters(raw: list[ScanFilterRequest]) -> list[ScanFilter]:
    parsed: list[ScanFilter] = []
    for item in raw:
        try:
            field = ScanField(item.field)
        except ValueError as exc:
            valid = ", ".join(f.value for f in ScanField)
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scan field {item.field!r}. Valid fields: {valid}.",
            ) from exc
        try:
            op = Op(item.op)
        except ValueError as exc:
            valid = ", ".join(o.value for o in Op)
            raise HTTPException(
                status_code=400,
                detail=f"Unknown operator {item.op!r}. Valid operators: {valid}.",
            ) from exc
        if op is Op.BETWEEN and item.value2 is None:
            raise HTTPException(
                status_code=400,
                detail=f"Filter on {item.field} uses 'between' but is missing value2.",
            )
        parsed.append(ScanFilter(field=field, op=op, value=item.value, value2=item.value2))
    return parsed


@router.post("")
def scan(request: ScanRequest, http_request: Request):
    """Run a scan. Every filter must pass (AND); a field that can't be
    measured for a symbol fails closed rather than matching by default."""
    # A scan fans out to up to MAX_SCAN_SYMBOLS provider fetches, the same
    # class of cost /analysis already rate-limits per caller.
    compute_limiter.enforce(http_request)

    try:
        timeframe = Timeframe.parse(request.timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filters = _parse_filters(request.filters)

    try:
        report = run_scan(
            request.symbols, timeframe, filters,
            bars=request.bars, provider=request.provider,
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return report.to_dict()


@router.get("/fields")
def fields():
    """Every field and operator the scanner understands, so the UI can build
    a filter form without hardcoding a copy of this list."""
    return {
        "fields": [f.value for f in ScanField],
        "operators": [o.value for o in Op],
        "notes": (
            "'between' requires both value and value2. 'trend' and 'volatility' are "
            "string fields and only support 'eq'. Every other field is numeric."
        ),
    }
