# Terms of Service

**Status: draft. Not yet reviewed by a lawyer.** The descriptions of what the
software does and does not do were checked against the code and are accurate.
Whether these terms are *enforceable* where you operate — and whether operating
this service at all requires a licence — has not been checked. See
[What still needs a lawyer](#14-what-still-needs-a-lawyer).

Placeholders in `{{BRACES}}` must be filled in before this is served to anyone.

---

**Operator:** {{OPERATOR_LEGAL_NAME}}
**Service:** Legend Trade, at {{SERVICE_URL}}
**Contact:** {{SUPPORT_CONTACT_EMAIL}}
**Governing law:** {{GOVERNING_LAW_JURISDICTION}}
**Last updated:** {{DATE}}

---

## 1. THIS IS NOT FINANCIAL ADVICE

**Read this section even if you read nothing else.**

Legend Trade is a market-analysis and simulation tool. Nothing it produces is
financial, investment, tax or legal advice, a recommendation to buy or sell any
instrument, or a solicitation of any kind. {{OPERATOR_LEGAL_NAME}} is not your
broker, adviser or fiduciary.

Specifically, and without limiting the above:

- **A "trade setup" is a calculation, not a recommendation.** The software
  computes an entry, stop and target from price data. That it can compute one
  is not evidence that taking it is wise.
- **A probability is an estimate from historical base rates.** It is not a
  forecast, a guarantee, or a statement about what will happen. Markets are not
  obliged to resemble their own history.
- **A backtest is not a prediction.** The platform's own Monte Carlo section
  exists specifically to show how wide the range of outcomes really is. A
  strategy that performed well on past data may lose money immediately.
- **An "APPROVED" audit verdict means the strategy passed fifteen mechanical
  checks.** It does not mean the strategy is profitable, safe, or suitable for
  you.

**Trading involves substantial risk of loss, including the loss of your entire
investment.** Leveraged instruments can lose more than you deposit. Past
performance does not indicate future results. You are solely responsible for
every decision you make and every trade you place. If you need advice, consult
a licensed professional in your jurisdiction.

## 2. Eligibility

You must be at least {{MINIMUM_AGE}} and legally able to enter this agreement.
You must not use the service where doing so would breach any law that applies
to you.

## 3. Accounts

You are responsible for keeping your password and, if enabled, your two-factor
recovery codes secure. Tell us promptly at {{SUPPORT_CONTACT_EMAIL}} if you
suspect unauthorised access.

Your webhook token is a secret. It appears in a URL you paste into third-party
alerting tools, so it is more likely to leak than a password — rotate it if you
suspect exposure. A webhook **never** places a trade on its own; it records an
alert.

Accounts may be suspended or terminated for breach of these terms, for abuse of
the service, or where required by law.

## 4. Simulated trading is the default

Ordinary accounts get **simulated (paper) trading only.** Simulated positions
involve no real money and no real orders. Simulated fills are modelled and will
differ from real execution: real markets have slippage, partial fills, gaps,
halts and outages that a simulation does not reproduce.

**Simulated results are not an indication of real results.**

## 5. Real broker execution, where enabled

Broker-connected trading is restricted to instance administrators, is disabled
by default, and requires a server-side flag, an explicit mode selection and a
typed confirmation phrase before any order can be transmitted.

If you are an administrator and you enable it, you accept that:

- **Orders are transmitted to a third-party broker under the operator's
  credentials, and are real.** They can lose real money.
- The broker's own terms, margin rules and risk disclosures apply to you
  independently of these terms.
- **No order has ever been placed through this software with a real broker.**
  The Alpaca adapter's authentication and account-reading path has been
  confirmed against a live Alpaca paper account, but submitting an order is a
  different endpoint and has only ever been exercised against recorded wire
  formats. The Tradier and Interactive Brokers adapters are unverified against
  live accounts entirely, and Interactive Brokers was implemented from
  reconstructed documentation. Test with a paper account first, and read
  `docs/TRADING_TERMINAL.md` before enabling live mode.
- Guardrails — mandatory stop losses, position and exposure limits, the audit
  gate and the kill switch — reduce risk but **do not eliminate it** and can
  fail. They are not a substitute for your own judgement.

## 6. Market data

Market data is supplied by third parties and provided "as is". It may be
delayed, incomplete, wrong or unavailable. The interface distinguishes live
streaming data (`LIVE`) from polled data (`DELAYED`) — **treat anything marked
`DELAYED` as stale and do not act on it as though it were a current quote.**

{{OPERATOR_LEGAL_NAME}} does not warrant data accuracy and is not liable for
losses arising from data errors, delays or gaps. Third-party data may carry its
own licence terms restricting redistribution.

## 7. Availability

The service is provided on an "as available" basis. There is no uptime
guarantee. It may be interrupted for maintenance, upstream provider failures,
or without notice.

**Do not rely on this service being reachable at a moment when you need to
manage a position.** Have an independent way to reach your broker.

## 8. Acceptable use

You agree not to:

- attempt to access another user's account or data;
- circumvent rate limits, authentication or the guardrail layer;
- scrape, resell or redistribute market data obtained through the service;
- use the service to break any law or any market's rules, including those on
  market manipulation;
- attack, overload or probe the infrastructure without written permission.

## 9. Intellectual property

The software is provided under {{SOFTWARE_LICENCE}}. Content you create —
watchlists, strategies, notes — remains yours. You grant
{{OPERATOR_LEGAL_NAME}} only the licence needed to store and display it back
to you.

## 10. Disclaimer of warranties

To the fullest extent permitted by law, the service is provided **"as is" and
"as available", without warranties of any kind**, express or implied, including
merchantability, fitness for a particular purpose, accuracy, and
non-infringement.

## 11. Limitation of liability

To the fullest extent permitted by law, {{OPERATOR_LEGAL_NAME}} is not liable
for any trading losses, lost profits, lost opportunity, or any indirect,
incidental, special, consequential or punitive damages arising from your use of
the service — including losses arising from data errors, downtime, simulated
results differing from real ones, or a guardrail failing to prevent an order.

Total aggregate liability is limited to {{LIABILITY_CAP}}.

Some jurisdictions do not allow these exclusions, and nothing here excludes
liability that cannot lawfully be excluded — including for death or personal
injury caused by negligence, or for fraud.

## 12. Changes

These terms may be updated. Material changes will be announced at
{{SERVICE_URL}} before taking effect. Continuing to use the service after that
means you accept the change.

## 13. Governing law

These terms are governed by the laws of {{GOVERNING_LAW_JURISDICTION}}, and
disputes are subject to the exclusive jurisdiction of its courts, except where
mandatory consumer-protection law in your country of residence gives you
different rights.

---

## 14. What still needs a lawyer

This draft describes the software accurately. It does not make operating the
service lawful. Before publishing, get qualified advice on:

1. **Whether providing trade setups and probabilities to the public is a
   regulated activity where you operate.** Depending on jurisdiction this can
   amount to investment advice or investment research and may require
   registration or a licence. **This is the highest-risk open question in this
   project.** Silence from a regulator is not permission, and a disclaimer
   saying "not financial advice" does not by itself make it so — regulators
   look at what the service actually does.
2. **Whether the liability cap and warranty disclaimer are enforceable** in
   your jurisdiction, particularly against consumers.
3. **Consumer-protection and distance-selling rules**, if you ever charge.
4. **Whether market-data vendor licences permit** the display and redistribution
   you are doing, especially for a public service rather than personal use.
5. **Governing law and dispute resolution** — the placeholder must become a
   real choice, and it needs to be one a court would respect.
6. **Whether operating broker-connected execution on behalf of others** —
   even administrators — engages further regulation.
