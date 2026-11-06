# Parchi — 75-second demo script

All audio used here is synthetic (Gnani Timbre v2.5) and already cached. If you follow the clicks exactly, the
recording makes **0 API calls**; check the sidebar counter before and after.

## Before recording (2 minutes)

1. Start on a fresh database so the order is `#1` and nothing old is on screen (PowerShell):
   ```powershell
   $env:PARCHI_DB = "data/demo_take1.db"   # any new file name; use take2, take3… for retakes
   .venv/Scripts/python -m streamlit run app/streamlit_app.py
   ```
2. Use a browser window around 1400 px wide, zoomed to 90%. Turn the system volume up: the replies are played on
   camera.
3. Keep these files ready to drag in: `data/samples/sample2.ogg`, `data/samples/reply_colgate.ogg` and
   `data/samples/reply_goodday.ogg`.

## Click path

| Time | Screen | Do this | Say this (voice-over) |
|---|---|---|---|
| 0–8 s | **Inbox** | Select **Gupta Kirana (R002)**. Drag `sample2.ogg` into *Voice notes*. Click **Process notes**. | "Kirana stores order by WhatsApp voice note, in Hinglish. Staff retype every line. Parchi doesn't." |
| 8–12 s | Inbox | Click **Review this order →**. | "Gnani Prisma v3.0 transcribes it, biased with the distributor's own catalog." |
| 12–28 s | **Review** | Click ▶ on the audio for 2 s, then stop. Point at the coloured transcript, then the table: 🟡 Colgate *which variant?*, 🟢 Thums Up 1.5 carton (*"dead peti"* was really *dedh peti*), 🟡 Good Day *qty missing*, 🔴 *lace*. Point at the 📝 retailer note. | "Every line is checked against the catalog. Green is safe. Amber needs one answer. Red, we never guess. And the complaint becomes a note, not an order line." |
| 28–38 s | **Reply & Clarify** (sidebar) | Click **🔊 Generate spoken reply**, then play it: *"…Colgate kaunsa chahiye, ek sau gram ya do sau gram? Good Day kitna bhejna hai?"* (play about 5 s). | "For the amber lines, Parchi asks the retailer, in their language, with Gnani Timbre v2.5." |
| 38–50 s | Reply & Clarify | Under *Retailer's answer*: on the **Colgate** line, upload `reply_colgate.ogg` and click **Resolve with this reply**: ✅ Colgate 200g. On the **Good Day** line, upload `reply_goodday.ogg` and click **Resolve**: ✅ 5 packets. | "The retailer answers by voice: 'do sau gram wala', 'paanch packet'. Both lines turn green." |
| 50–62 s | **Review** | On line 4 (🔴 *lace*), open the product dropdown and pick **Lay's India's Magic Masala · 52g ★**. Click **Save changes**, then **Approve order**. | "Lace means Lay's: the clerk picks it from the suggestions. And a human always approves. Parchi never auto-approves." |
| 62–68 s | **Reply & Clarify** | Tick **Include estimated total**. Click **🔊 Generate spoken reply** and play the start: *"Gupta Kirana ji, aapka order mila: Colgate do sau gram chaubis piece…"* | "And the retailer gets a spoken confirmation." |
| 68–72 s | **Export** | Show the table and click **Download CSV**. | "Tally-ready, no retyping." |
| 72–75 s | **Evaluation** | Show the F1 and unsafe auto-approve charts. | "Measured: catalog biasing gets every product right on phone-quality audio. Parchi, on Gnani Prisma and Timbre." |

## If something goes wrong

- **The sidebar API counter went up.** A click differed from the script (for example a different Lay's variant, or
  the total box ticked at a different time), so the reply text changed and was synthesised fresh. That's harmless:
  it costs 1 call and is cached for the next take.
- **"Transcription failed — Audio attached, please listen".** The API is unreachable or the key is missing. The
  sidebar warns about a missing key. Everything above is cached, so this only happens with new audio.
- **For a live-recording variant**, record your own voice saying the sample2 sentence and upload that instead. It
  costs 1 Prisma call, and the table appears within a few seconds.
