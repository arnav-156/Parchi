# 🧾 Parchi — Hinglish voice notes → verified orders → spoken replies

*Gnani AI Great Indian AI Internship Challenge 2026 · built on **Gnani Prisma v3.0** and **Gnani Timbre v2.5***

**🎥 Demo (1:38):** [docs/Parchi_Demo.mp4](docs/Parchi_Demo.mp4) · **📄 PDF:** [docs/Parchi_Pitch.pdf](docs/Parchi_Pitch.pdf) · **💻 Code:** [github.com/arnav-156/Parchi](https://github.com/arnav-156/Parchi)

## Problem
India's independent FMCG distributors each serve 100–300 kirana stores. Retailers order by WhatsApp voice note, in
Hinglish: *"Parle-G ke do carton, Maggi chhota wala paanch dabba…"*. Staff listen to every note and retype each line
into Tally. Retailers rarely get a confirmation, and every slip becomes a wrong delivery.

## Solution
Parchi is an order desk that turns those notes into a verified order sheet, and **talks back to the retailer** in
their language.

1. **Hear** — the note is transcribed by **Gnani Prisma v3.0** (`indic-auto`, `codemix`), biased with the
   distributor's own catalog as keywords, so brand names come out right.
2. **Understand** — deterministic rules parse Hindi/English numbers, fractions (*dedh, saadhe*) and trade units
   (*peti, dabba, darjan*), then match each line to a SKU. Complaints (*"pichli baar kam aaya"*) become notes.
3. **Flag, don't guess** — 🟢 confident · 🟡 needs one answer (which pack? how many?) · 🔴 not in catalog.
4. **Ask back** — for amber lines, **Gnani Timbre v2.5** speaks a Hinglish question (*"Colgate kaunsa chahiye,
   sau gram ya do sau gram?"*). The retailer's voice reply resolves the line.
5. **Confirm & export** — a human approves (never automatic), the retailer gets a spoken confirmation, and the
   distributor gets a Tally-ready CSV.

## Results (synthetic test set, 59 order lines)
| | Without catalog biasing | **With Parchi biasing** |
|---|---|---|
| Line F1, phone-quality audio | 0.800 | **0.820** |
| Product (SKU) accuracy, phone audio | 94.9% | **100%** |
| Wrong lines auto-marked green | 4.3% | **3.8%** |

End-to-end browser test with synthetic phone calls: **16/16 pass, 0 wrong lines approved**. Prisma returns a
transcript in ~0.6 s per note. 532 automated tests; every Gnani call is cached and counted.

## What's real vs mocked
Real: Prisma v3.0 transcription, Timbre v2.5 speech, parsing, matching, review, clarification loop, export.
Mocked: WhatsApp (file upload) and Tally (CSV). All data is synthetic — no real customers or recordings.

**Next:** WhatsApp Business API, direct Tally push, and a held-out test set of real retailer recordings.
