# AI Desk — Personal AI Council

Three models answer the same question independently, then (later) review each
other and reach a synthesized answer.

**Phase 1 is the whole repo right now.** It proves one thing only: that we can
get three independent answers in parallel, measure tokens, cost and latency,
and keep going when one head fails. No peer review, no judge, no synthesis, no
UI, no database — those come after the numbers say the council is worth it.

## Heads

| Head | Access | Cost |
|---|---|---|
| Anthropic | Agent SDK on the Claude plan credit, falling back to the API | covered by the plan, then metered |
| Google | Gemini API (AI Studio) | free tier, then metered |
| OpenAI | OpenAI API | metered — no subscription grants API access |

A ChatGPT Plus or Google AI Pro subscription does **not** include API access;
only the Claude plan converts into programmatic use, via its Agent SDK credit.

## Setup

Clone first, and run everything from inside the clone — `pip install -e .`
reads `pyproject.toml` from the current directory.

```bash
git clone https://github.com/apop14520-glitch/ai-desk.git
cd ai-desk
```

Linux / macOS:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # then fill it in
```

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
pip install -e .
Copy-Item .env.example .env    # then fill it in
```

For the Anthropic head on the plan credit, sign in with the Claude CLI once —
the Agent SDK reads that session, so no key is needed. Set `ANTHROPIC_VIA=api`
and an `ANTHROPIC_API_KEY` to use the metered API instead.

Set a hard spend limit on the OpenAI key in their dashboard. That limit is a
stronger guarantee than any budget check in this code.

## Run

```bash
python -m council.poc                      # default prompt
python -m council.poc "your question"
```

Writes one JSON per head plus a `summary.json` to `out/<timestamp>/`, and
prints a table of latency, tokens and cost.

## Success criteria

- [ ] all three heads answer
- [ ] answers are independent (no head sees another's output)
- [ ] the three calls run in parallel
- [ ] a failing head is captured, not fatal
- [ ] latency, tokens and cost are recorded per head

Test the failure path on purpose: break one key in `.env` and confirm the run
still reports `PARTIAL: 2/3` and exits 0.

## Prices

`PRICES` in `council/providers.py` is a hand-maintained table with a
`CHECKED_ON` date. Costs are only as honest as that table — re-check it against
the provider dashboards before trusting a number. A model missing from the
table reports `n/a`, never `0.00`.

## Next

Phase 2 adds anonymized peer review (shuffled labels, no self-review),
aggregated ranking, and a deterministic judge — plus a benchmark that answers
the only question that matters: does the council beat the best single model?
