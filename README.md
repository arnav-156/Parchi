# Parchi — voice-note order desk for FMCG distributors

Parchi turns Hinglish WhatsApp voice notes from kirana retailers into a verified order sheet, asks the
retailer a spoken question when a line is unclear, and replies with a spoken confirmation.

Built for the Gnani AI *Great Indian AI Internship Challenge 2026* with:

- **Gnani Prisma v3.0** (speech-to-text) — `indic-auto` with hi-IN/en-IN candidates, `codemix` output,
  the distributor's catalog as `keywords` (≤200) and an order-taking `topic`.
- **Gnani Timbre v2.5** (text-to-speech) — Hinglish replies (voice *Poorvi*, `hi-IN`) as `.ogg` (Opus, WhatsApp
  voice-note format) plus a `.wav` for in-app playback.

## The problem

An independent FMCG distributor serves 100–300 kirana stores. Retailers order by WhatsApp voice note in
Hinglish — *"Parle-G ke do carton, Maggi chhota wala paanch dabba…"*. Staff listen to every note and retype each
line into Tally or a spreadsheet. Retailers rarely get a confirmation, and mistakes turn into wrong deliveries.

Parchi removes the retyping. It flags anything uncertain for a human instead of guessing, and closes the loop
with the retailer by voice, in their language.

## How it works

```
voice note ─► ffmpeg (16 kHz mono) ─► Prisma v3.0 (catalog keywords + topic, ≤28 s chunks, cached)
          ─► romanise Devanagari (offset map kept) ─► rule-based parser (Hindi numbers, fractions, units,
             complaint notes) ─► fuzzy catalog matcher ─► GREEN / AMBER / RED lines ─► clerk review
          ─► approve (always a human click) ─► Tally-style CSV
          └► reply templates ─► Timbre v2.5 (.ogg + .wav) ─► retailer's answer note ─► line resolved
```

- **GREEN**: unique catalog match, score ≥ 85, quantity and unit present.
- **AMBER**: needs review. The reason is one of:
  - `ambiguous_variant` (e.g. Colgate 100 g or 200 g?)
  - `qty_missing`
  - `unit_missing` (a unit is suggested)
  - `low_confidence`
  - `qty_suspicious` (more than 500)
  - `unit_mismatch` (e.g. kilos of toothpaste)
- **RED**: `unmatched`. The clerk picks the product from a suggestion list.
- **Complaints** (*"pichli baar kam aaya tha"*) become order notes, not lines.
- **No line is ever approved automatically.** An order can't be approved or exported while any AMBER or RED line is
  unresolved and not removed.

Parsing and matching are deterministic: no LLM in the default path. They are pure functions with unit tests.

## Run it

Needs Python 3.11+ (developed on 3.12) and `ffmpeg` on PATH. On Windows: `winget install Gyan.FFmpeg`.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # macOS/Linux: .venv/bin/python
cp .env.example .env                                          # then put your key in GNANI_API_KEY=
.venv/Scripts/python scripts/smoke_test.py                    # 1 STT + 1 TTS call, prints raw responses
.venv/Scripts/python -m streamlit run app/streamlit_app.py    # the app
```

The app has five pages:

- **Inbox**: upload notes for a retailer.
- **Review**: audio, highlighted transcript, editable lines, Approve.
- **Reply & Clarify**: spoken question or confirmation, and upload of the retailer's answer.
- **Export**: CSV download.
- **Evaluation**: the latest accuracy report.

CLI:

```bash
.venv/Scripts/python -m parchi.pipeline data/samples/sample1.ogg     # parse one note, print the order table
.venv/Scripts/python -m parchi.eval synth --n 20                      # synthetic test notes (Timbre, 1 call each)
.venv/Scripts/python -m parchi.eval run --conditions all --configs A,B
.venv/Scripts/python -m parchi.eval report                            # rebuild the latest report, no API
.venv/Scripts/python -m pytest -q                                     # 532 tests; none call the API
.venv/Scripts/python scripts/make_noise.py                            # synthetic fan/traffic/crowd noise, no API
.venv/Scripts/python scripts/make_test_calls.py                       # 5 synthetic retailer calls (Timbre + phone line)
```

**Credits.** Every STT and TTS call is cached:
- STT results are keyed by audio hash + request config.
- TTS audio is keyed by text + voice + model + speed + format.

Re-processing the same audio makes zero API calls. Real calls are counted in `cache/usage.json`. They are refused
once `PARCHI_MAX_API_CALLS` (default 600) is reached, and the sidebar shows the count. The eval CLI prints its
estimated new calls first, and refuses more than 100 without `--yes`.

Environment variables (`.env`):

| Variable | Default | Meaning |
|---|---|---|
| `GNANI_API_KEY` | — | Gnani key (never printed or logged) |
| `PARCHI_MAX_API_CALLS` | 600 | hard cap on real API calls |
| `PARCHI_TTS_VOICE` / `PARCHI_TTS_VOICE_EN` | Poorvi / Kaveri | Timbre v2.5 voices for hi-IN / en-IN replies |
| `PARCHI_TTS_SPEED` | 0.95 | reply speed |
| `PARCHI_DISTRIBUTOR` | Shree Ganesh Traders | name used in replies |
| `PARCHI_DB` | data/parchi.db | SQLite file (point at a new file for a clean demo) |

## What is mocked

- **WhatsApp**: notes come in through file upload, and replies are downloadable `.ogg` voice notes. There is no
  WhatsApp, Twilio or Business API integration.
- **Tally**: the export is a Tally-friendly CSV, one row per line. It has voucher number, party, item code and
  name, the quantity as ordered, the quantity billed in the base unit, rate and amount. Nothing is pushed into
  Tally.
- **Data**: everything is synthetic (challenge rule).
  - The 72-SKU catalog (`data/catalog.csv`) has realistic brands, but **MRPs are approximate demo values**.
  - The retailers (`data/retailers.csv`) are made up and have no phone numbers.
  - Every voice note in `data/` was generated with Timbre v2.5.

## Evaluation

`python -m parchi.eval run` scores every labelled note in `data/testset/` across conditions and configs.

- **Conditions:**
  - `clean`
  - `phone`: 8 kHz, μ-law, 300–3400 Hz band-pass
  - `phone+noise@10dB` and `phone+noise@5dB`: noise from `data/noise/*.wav`, mixed by RMS
- **Configs:**
  - `A_baseline`: Prisma v3.0 with no keywords and no topic
  - `B_parchi`: the same, plus catalog keywords and topic
- **Metrics:**
  - **Line precision, recall and F1**: a line counts as correct only if SKU, quantity and unit all match the label.
  - **SKU accuracy and quantity accuracy**.
  - **Unsafe auto-approve rate**: the share of GREEN lines that are wrong. This is the key safety number.
  - **Clarification rate**: the share of lines marked AMBER.
- **Output:** `reports/eval_<timestamp>/` holds `results.csv`, `per_note.csv`, `report.md` (with the top-10
  failures) and the charts. Human and synthetic notes are always reported separately.

**Latest numbers** (`reports/eval_20261106_150514`, 20 synthetic notes, 59 lines):

| Condition | Config | F1 | SKU acc | Qty acc | Unsafe auto-approve | Clarification rate |
|---|---|---|---|---|---|---|
| clean | A_baseline | 0.860 | 96.6% | 91.2% | 2.0% | 12.9% |
| clean | B_parchi | **0.876** | 98.3% | 91.4% | **1.9%** | 9.7% |
| phone | A_baseline | 0.800 | 94.9% | 85.7% | 4.3% | 16.4% |
| phone | B_parchi | **0.820** | **100%** | 84.7% | **3.8%** | 11.1% |

What the numbers show:

- **Catalog biasing mostly fixes brand names.** With B, every SKU was right on phone audio.
- **Most remaining errors are misheard number words**, e.g. *pachees* → *paanch*, *sattar* → *"Satta"*.
- **Noise conditions were skipped in this run.** Synthetic noise now exists in `data/noise/` (made by
  `scripts/make_noise.py`, no real recordings), so the next `run` includes them: about 80 more Prisma calls.
- **There are no human recordings yet.** To add one, a teammate reads a script from `data/testset/scripts.csv`,
  then registers it with `python -m parchi.eval add-human syn_003 take.ogg`.
- **Treat the synthetic numbers as a development set.** The synthetic scripts share vocabulary with the parser,
  and the rules were tuned after reading these failures. Human recordings are the held-out check.

## Known limitations

- **Misheard quantities can still go GREEN.** If Prisma hears *paanch* for *pachees*, the line is
  well-formed but wrong. That is the 2–4% unsafe rate above. The clerk's review and the spoken confirmation are the
  safety net.
- **The rules can misattach a quantity.** In *"Good Day teen darjan Pepsi"*, "teen darjan" goes to the first
  product (Good Day). A quantity said before a comma attaches only to the product right after it.
- **Long notes**: notes over 28 s are split at silence and the chunks are sent with the earlier text as context.
  This is tested with synthetic audio only, not real long voice notes.
- **One request at a time**: the Gnani API allows a single active request. The client waits 0.5 s between STT
  calls and backs off 5/10/20 s on HTTP 429.
- **Clarification** works one line at a time: the retailer's answer is matched only against that line's
  candidates. The same answer audio can be uploaded for several lines (cache hit). RED lines are left to the clerk
  and are not asked about.
- **Export units**: `BOX` (*dabba*) counts as one sale unit. `DOZEN` becomes pieces only when the SKU is sold by the
  piece.
- **Clerk confirmation**: a line the clerk edits or ticks OK becomes GREEN as long as SKU, quantity and unit are
  filled in. The unit check is not re-run.
- **Reply languages**: only Hinglish and English templates exist. Any other detected language gets a Hinglish reply,
  and the fallback is recorded.
- **Not built** (out of scope): no authentication, single user, SQLite only, no deployment, no LLM fallback.

## Layout

```
parchi/        config, gnani_client (the only Gnani caller), audio, translit, lexicon/*.csv, parser, catalog,
               matcher, pipeline, db, export, templates, reply, eval/{channel,metrics,synth,run,report}
app/           streamlit_app.py
data/          catalog.csv, retailers.csv, samples/, testset/{audio,labels.csv,scripts.csv}, noise/
scripts/       smoke_test.py, make_noise.py, make_test_calls.py
qa/            Parchi_Test_Plan.xlsx (test cases, results, findings, human recording kit, API budget), screenshots/
tests/         parser, matcher, translit, templates, channel, metrics, export, pipeline (mocked), eval (mocked), app
```
