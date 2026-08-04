import { useEffect, useState } from "react";
import { trading } from "../../lib/tradingApi.js";

/**
 * Research mode.
 *
 * Two things share this panel because they answer two different kinds of
 * question. The glossary answers "what is a CHOCH" and works offline with no
 * API key. Grounded research answers "why did price reject here" and needs a
 * live analysis of the current symbol to reason over.
 */
export default function ResearchPanel({ symbol, timeframe }) {
  const [question, setQuestion] = useState("");
  const [audience, setAudience] = useState("intermediate");
  const [grounded, setGrounded] = useState(true);
  const [answer, setAnswer] = useState(null);
  const [concepts, setConcepts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    trading.concepts().then((data) => setConcepts(data.concepts)).catch(() => {});
  }, []);

  const ask = async (event) => {
    event?.preventDefault();
    const text = question.trim();
    if (!text) return;

    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(
        await trading.research({
          question: text,
          symbol: grounded ? symbol : null,
          timeframe,
          audience,
        }),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const openConcept = async (key) => {
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(await trading.concept(key, audience));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-term-panel">
      <form onSubmit={ask} className="p-2 border-b border-term-border space-y-1.5">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) ask(e);
          }}
          rows={2}
          placeholder="Why did price reject here? Where is liquidity? Explain CHOCH…"
          className="w-full bg-term-raised border border-term-border rounded px-2 py-1.5 text-[11px]
                     text-term-text placeholder:text-term-dim resize-none leading-relaxed
                     focus:outline-none focus:border-brand-accent"
        />
        <div className="flex items-center gap-1.5">
          <select
            value={audience}
            onChange={(e) => setAudience(e.target.value)}
            className="bg-term-raised border border-term-border rounded px-1.5 py-1 text-[10px] text-term-text"
          >
            <option value="beginner">Beginner</option>
            <option value="intermediate">Intermediate</option>
            <option value="professional">Professional</option>
          </select>
          <label className="flex items-center gap-1 text-[10px] text-term-muted cursor-pointer">
            <input
              type="checkbox"
              checked={grounded}
              onChange={(e) => setGrounded(e.target.checked)}
              className="accent-brand-accent"
            />
            Use {symbol}
          </label>
          <button type="submit" disabled={busy} className="btn-term-primary text-[10px] ml-auto">
            {busy ? "Thinking…" : "Ask"}
          </button>
        </div>
      </form>

      <div className="overflow-y-auto flex-1">
        {error && (
          <div className="m-2 p-2 rounded border border-term-down/40 bg-term-down/10">
            <p className="text-[11px] text-term-down leading-relaxed">{error}</p>
          </div>
        )}

        {busy && (
          <p className="p-3 text-[11px] text-term-muted animate-pulse">
            {grounded ? "Analysing the chart, then answering…" : "Answering…"}
          </p>
        )}

        {answer && <Answer answer={answer} />}

        {!answer && !busy && (
          <div className="p-3">
            <p className="text-[10px] uppercase tracking-wide text-term-dim mb-2">
              Concept glossary — works offline
            </p>
            <div className="space-y-1">
              {concepts.map((c) => (
                <button
                  key={c.key}
                  onClick={() => openConcept(c.key)}
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-term-raised transition-colors"
                >
                  <span className="text-[11px] text-term-text font-medium block">{c.title}</span>
                  <span className="text-[10px] text-term-muted leading-relaxed block">{c.short}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Answer({ answer }) {
  const body = answer.answer ?? answer.explanation ?? answer.message ?? "";
  const title = answer.title ?? answer.question ?? "Answer";

  return (
    <div className="p-3">
      <p className="text-[11px] font-semibold text-term-text mb-1">{title}</p>
      {answer.short && <p className="text-[10px] text-term-muted italic mb-2">{answer.short}</p>}
      <p className="text-[11px] text-term-text leading-relaxed whitespace-pre-wrap">{body}</p>

      {answer.grounded && (
        <p className="text-[10px] text-term-info mt-2">
          Answered from a live analysis of the current chart.
        </p>
      )}
      {answer.source === "glossary" && (
        <p className="text-[10px] text-term-dim mt-2">From the built-in glossary.</p>
      )}
      {answer.note && (
        <p className="text-[10px] text-term-warn leading-relaxed mt-2">{answer.note}</p>
      )}
    </div>
  );
}
