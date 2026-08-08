import { useCallback, useEffect, useState } from "react";
import { calendar } from "../../lib/tradingApi.js";

/**
 * Economic calendar: scheduled data releases from FRED.
 *
 * Importance here is an estimate, not something FRED provides — a
 * hand-maintained keyword match against well-known release names (see
 * trading/econcalendar.py). Shown as "estimated importance" rather than a
 * bare label so it never reads as an official rating.
 */
const IMPORTANCE_STYLE = {
  high: "text-term-down border-term-down/40 bg-term-down/10",
  medium: "text-yellow-500 border-yellow-500/40 bg-yellow-500/10",
  low: "text-term-dim border-term-border bg-term-raised",
};

export default function CalendarPanel() {
  const [days, setDays] = useState(14);
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const data = await calendar.upcoming(days);
      setEvents(data.events);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [days]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col h-full bg-term-panel">
      <div className="px-3 py-2 border-b border-term-border flex items-center justify-between">
        <p className="text-[10px] uppercase tracking-wide text-term-dim">
          Upcoming releases (estimated importance)
        </p>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="bg-term-raised border border-term-border rounded px-1.5 py-1 text-[10px] text-term-text"
        >
          {[7, 14, 30, 60].map((d) => (
            <option key={d} value={d}>{d}d</option>
          ))}
        </select>
      </div>

      <div className="overflow-y-auto flex-1 p-2">
        {busy && <p className="text-[10px] text-term-dim px-1">Loading…</p>}

        {error && (
          <div className="m-1 p-2 rounded border border-term-down/40 bg-term-down/10">
            <p className="text-[11px] text-term-down leading-relaxed">
              {error}
              {error.includes("FRED_API_KEY") && (
                <span className="block mt-1 text-term-dim">
                  Free registration: fred.stlouisfed.org/docs/api/api_key.html
                </span>
              )}
            </p>
          </div>
        )}

        {events && events.length === 0 && !error && (
          <p className="text-[10px] text-term-dim px-1">No scheduled releases in this window.</p>
        )}

        {events?.map((event) => (
          <div
            key={`${event.release_id}-${event.date}`}
            className="flex items-center justify-between gap-2 px-2 py-1.5 mb-1 rounded border border-term-border"
          >
            <div className="min-w-0">
              <p className="text-[11px] text-term-text truncate">{event.name}</p>
              <p className="text-[9px] text-term-dim">{event.date}</p>
            </div>
            <span
              className={`shrink-0 text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${IMPORTANCE_STYLE[event.importance]}`}
            >
              {event.importance}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
