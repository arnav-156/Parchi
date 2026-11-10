"""Parchi — voice-note order desk. Run: streamlit run app/streamlit_app.py"""
from __future__ import annotations

import hashlib
import html
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from parchi import config, db, gnani_client, pipeline, reply  # noqa: E402
from parchi.catalog import load_catalog  # noqa: E402
from parchi.export import export_csv  # noqa: E402

UPLOAD_DIR = config.DATA_DIR / "uploads"
AUDIO_TYPES = ["ogg", "opus", "m4a", "mp3", "wav"]
UNITS = ["CARTON", "BOX", "PACKET", "DOZEN", "PIECE", "STRIP", "KG", "G", "L", "ML", "SACK"]
COLOURS = {"GREEN": "#c8f7c5", "AMBER": "#ffe08a", "RED": "#ffb3b3"}
BADGE = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}
REASON_TEXT = {
    "ambiguous_variant": "which variant?", "low_confidence": "unsure match", "qty_missing": "qty missing", "unit_missing": "unit not said",
    "qty_suspicious": "qty looks too high", "unit_mismatch": "unit doesn't fit product", "unmatched": "not in catalog",
}
PAGES = ["Inbox", "Review", "Reply & Clarify", "Export", "Evaluation"]

st.set_page_config(page_title="Parchi", page_icon="🧾", layout="wide")


@st.cache_resource
def get_conn(path: str):
    return db.connect(path)


conn = get_conn(str(config.DB_PATH))
cat = load_catalog()
if "page" not in st.session_state:
    st.session_state.page = "Inbox"


def go(page: str, order_id: int | None = None) -> None:
    st.session_state.page = page
    if order_id is not None:
        st.session_state.order_id = order_id


def retailer_names() -> dict[str, str]:
    return {r["id"]: r["name"] for r in db.rows(conn, "SELECT id, name FROM retailers ORDER BY id")}


def sku_label(sku_id: str | None) -> str:
    if not sku_id:
        return "— choose product —"
    s = cat.by_id[sku_id]
    return f"{s.product} · {s.pack_size} ({s.sku_id})"


def highlight(transcript: str, lines: list[dict]) -> str:
    """Transcript HTML with each line's span coloured by its status."""
    spans = sorted((ln["span_start"], ln["span_end"], ln) for ln in lines
                   if ln["span_start"] is not None and not ln["removed"])
    out, pos = [], 0
    for s, e, ln in spans:
        if s < pos:
            continue
        out.append(html.escape(transcript[pos:s]))
        out.append(f'<mark style="background:{COLOURS[ln["status"]]};padding:1px 3px;border-radius:3px" '
                   f'title="line {ln["line_no"]}">{html.escape(transcript[s:e])}<sup>{ln["line_no"]}</sup></mark>')
        pos = e
    out.append(html.escape(transcript[pos:]))
    return f'<div style="font-size:1.1rem;line-height:2">{"".join(out)}</div>'


def save_upload(f) -> Path:
    data = f.getvalue()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    p = UPLOAD_DIR / f"{hashlib.sha256(data).hexdigest()[:16]}{Path(f.name).suffix.lower()}"
    if not p.exists():
        p.write_bytes(data)
    return p


def env_warnings() -> None:
    """Tell the clerk up front what won't work (never shows the key itself)."""
    import os
    import shutil

    if not os.getenv("GNANI_API_KEY"):
        st.sidebar.warning("GNANI_API_KEY is not set in .env — only notes already in the cache can be processed.")
    if not shutil.which("ffmpeg"):
        st.sidebar.error("ffmpeg not found on PATH — audio cannot be decoded.")


def usage_caption() -> None:
    u = gnani_client.usage()
    st.sidebar.caption(f"Gnani API calls: **{u['total']}** / {config.MAX_API_CALLS}  \n"
                       f"Prisma v3.0 STT: {u.get('stt', 0)} · Timbre v2.5 TTS: {u.get('tts', 0)}")


# ---------------- Inbox ----------------

def page_inbox() -> None:
    st.header("Inbox")
    st.caption("WhatsApp is mocked: upload the retailer's voice notes here.")
    names = retailer_names()
    with st.form("upload", clear_on_submit=True):
        rid = st.selectbox("Retailer", list(names), format_func=lambda r: f"{names[r]} ({r})")
        files = st.file_uploader("Voice notes", type=AUDIO_TYPES, accept_multiple_files=True)
        go_btn = st.form_submit_button("Process notes", type="primary")
    if go_btn:
        if not files:
            st.warning("Upload at least one voice note.")
        else:
            order_id = None
            failed = 0
            with st.status(f"Transcribing {len(files)} note(s) with Gnani Prisma v3.0…", expanded=True) as status:
                for f in files:
                    t0 = time.perf_counter()
                    try:
                        order_id, note_id = pipeline.process_note(conn, save_upload(f), rid, order_id, f.name)
                        note = db.row(conn, "voice_notes", note_id)
                        if note["status"] == "STT_FAILED":
                            failed += 1
                            st.error(f"{f.name}: {note['error']}")
                        else:
                            src = "cache" if note["api_calls"] == 0 else f"{note['api_calls']} API call(s)"
                            st.write(f"✅ {f.name} — {time.perf_counter() - t0:.1f}s ({src})")
                    except Exception as exc:  # never crash the UI
                        failed += 1
                        st.error(f"{f.name}: could not process ({exc})")
                # keep the per-file results open: a collapsed box hid upload errors from the clerk
                status.update(label=f"Done — {len(files) - failed} processed, {failed} need attention" if failed
                              else "Done", state="error" if failed else "complete", expanded=True)
            if order_id:
                st.session_state.order_id = order_id
                st.button("Review this order →", type="primary", on_click=go, args=("Review", order_id))

    st.subheader("Orders")
    orders = db.rows(conn, """
        SELECT o.id, o.retailer_id, o.status, o.created_at,
          SUM(CASE WHEN l.status='GREEN' AND l.removed=0 THEN 1 ELSE 0 END) AS green,
          SUM(CASE WHEN l.status='AMBER' AND l.removed=0 THEN 1 ELSE 0 END) AS amber,
          SUM(CASE WHEN l.status='RED' AND l.removed=0 THEN 1 ELSE 0 END) AS red
        FROM orders o LEFT JOIN order_lines l ON l.order_id = o.id
        GROUP BY o.id ORDER BY o.id DESC LIMIT 50""")
    if not orders:
        st.info("No orders yet.")
        return
    for o in orders:
        c1, c2, c3, c4 = st.columns([1, 3, 3, 1])
        c1.write(f"**#{o['id']}**")
        c2.write(f"{names.get(o['retailer_id'], o['retailer_id'])}  \n{o['created_at'].replace('T', ' ')}")
        c3.write(f"{o['status']} · 🟢 {o['green'] or 0} · 🟡 {o['amber'] or 0} · 🔴 {o['red'] or 0}")
        c4.button("Open", key=f"open{o['id']}", on_click=go, args=("Review", o["id"]))


# ---------------- Review ----------------

def apply_edits(order_id: int, lines: list[dict]) -> None:
    for ln in lines:
        k = ln["id"]
        sku, qty, unit, removed = (st.session_state[f"sku{k}"], st.session_state[f"qty{k}"],
                                   st.session_state[f"unit{k}"], st.session_state[f"rm{k}"])
        confirmed = st.session_state.get(f"ok{k}", False)
        qty = qty if qty and qty > 0 else None
        changed = (sku, qty, unit, int(removed)) != (ln["sku_id"], ln["qty"], ln["unit"], ln["removed"])
        if not changed and not (confirmed and ln["status"] != "GREEN"):
            continue
        fields = {"sku_id": sku, "qty": qty, "unit": unit, "removed": int(removed), "edited": 1}
        # an edited or ticked line with product + qty + unit is resolved by the clerk; missing fields stay flagged
        if sku and qty and unit:
            fields.update(status="GREEN", reasons=[], resolved_by="clerk")
        else:
            missing = (["unmatched"] if not sku else []) + (["qty_missing"] if not qty else [])
            fields.update(status="AMBER" if sku else "RED", reasons=missing or ln["reasons"])
        db.update(conn, "order_lines", k, **fields)
        db.log_event(conn, "line_edited", order_id, line_id=k, **{x: fields[x] for x in ("sku_id", "qty", "unit", "removed")})
    st.toast("Changes saved")


def line_editor(order_id: int, lines: list[dict], locked: bool) -> None:
    all_ids = [s.sku_id for s in cat.skus]
    widths = [0.4, 1.1, 1.5, 3.2, 1, 1.2, 0.6, 0.6, 0.7]
    hdr = st.columns(widths)
    for c, h in zip(hdr, ["#", "Status", "Heard", "Product", "Qty", "Unit", "Score", "OK", "Remove"]):
        c.markdown(f"**{h}**")
    for ln in lines:
        k = ln["id"]
        c = st.columns(widths)
        c[0].write(ln["line_no"])
        why = ", ".join(REASON_TEXT.get(r, r) for r in ln["reasons"])
        c[1].markdown(f"{BADGE[ln['status']]} {ln['status']}" + (f"  \n<small>{why}</small>" if why else ""),
                      unsafe_allow_html=True)
        c[2].write(ln["product_text"] or "—")
        cands = [x for x in ln["candidates"] if x in cat.by_id]
        options = [None, *cands, *[x for x in all_ids if x not in cands]]
        labels = {x: sku_label(x) + (" ★" if x in cands else "") for x in options}  # ★ = suggested candidate
        c[3].selectbox("SKU", options, index=options.index(ln["sku_id"]) if ln["sku_id"] in options else 0,
                       format_func=labels.__getitem__, key=f"sku{k}", label_visibility="collapsed", disabled=locked)
        c[4].number_input("Qty", min_value=0.0, step=1.0, value=float(ln["qty"] or 0), key=f"qty{k}",
                          label_visibility="collapsed", disabled=locked)
        unit_opts = [None, *UNITS]
        c[5].selectbox("Unit", unit_opts, index=unit_opts.index(ln["unit"]) if ln["unit"] in unit_opts else 0,
                       format_func=lambda u: u or "—", key=f"unit{k}", label_visibility="collapsed", disabled=locked)
        c[6].write(f"{ln['score']:.0f}" if ln["score"] is not None else "")
        if ln["status"] != "GREEN":
            c[7].checkbox("OK", key=f"ok{k}", label_visibility="collapsed", disabled=locked,
                          help="I checked this line: product, qty and unit are right")
        c[8].checkbox("Remove", value=bool(ln["removed"]), key=f"rm{k}", label_visibility="collapsed", disabled=locked)


def page_review() -> None:
    st.header("Review")
    names = retailer_names()
    orders = db.rows(conn, "SELECT * FROM orders ORDER BY id DESC LIMIT 100")
    if not orders:
        st.info("No orders yet — upload voice notes in the Inbox.")
        return
    ids = [o["id"] for o in orders]
    cur = st.session_state.get("order_id", ids[0])
    order_id = st.selectbox("Order", ids, index=ids.index(cur) if cur in ids else 0,
                            format_func=lambda i: f"#{i} — {names.get(db.row(conn, 'orders', i)['retailer_id'])}")
    st.session_state.order_id = order_id
    order = db.row(conn, "orders", order_id)
    lines = db.order_lines(conn, order_id)
    locked = order["status"] != "DRAFT"

    notes = db.rows(conn, "SELECT * FROM voice_notes WHERE order_id = ? ORDER BY id", (order_id,))
    for n in notes:
        title = f"🎙️ {n['filename']}" + (" (clarification reply)" if n["kind"] == "clarification" else "")
        with st.expander(title, expanded=n["kind"] == "order"):
            if n["audio_path"] and Path(n["audio_path"]).exists():
                st.audio(n["audio_path"])
            if n["status"] == "STT_FAILED":
                st.error(f"Transcription failed. {n['error'] or pipeline.STT_FAILED_MSG}")
                continue
            note_lines = [ln for ln in lines if ln["note_id"] == n["id"]]
            st.markdown(highlight(n["transcript"] or "", note_lines), unsafe_allow_html=True)
            meta = [f"language: {n['detected_lang'] or '?'}"]
            if n["latency_s"] is not None:
                meta.append(f"processed in {n['latency_s']:.1f}s")
            meta.append("from cache" if not n["api_calls"] else f"{n['api_calls']} API call(s)")
            st.caption(" · ".join(meta))
            for complaint in n["notes_json"] or []:
                st.warning(f"📝 Retailer note: {complaint}")

    if not lines:
        st.info("No order lines were found in this order's notes.")
        return
    st.subheader(f"Order lines — {order['status']}")
    line_editor(order_id, lines, locked)
    if locked:
        st.success(f"Order {order['status'].lower()} — edits are locked.")
        if order["status"] == "APPROVED":
            c1, c2, _ = st.columns([1, 1, 3])
            c1.button("Send confirmation →", on_click=go, args=("Reply & Clarify",))
            c2.button("Go to Export →", on_click=go, args=("Export",))
        return
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Save changes"):
        apply_edits(order_id, lines)
        st.rerun()
    open_lines = db.unresolved_lines(conn, order_id)
    if c2.button("Approve order", type="primary", disabled=bool(open_lines)):
        apply_edits(order_id, lines)
        try:
            db.approve_order(conn, order_id)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if open_lines:
        st.caption(f"⚠️ {len(open_lines)} line(s) need review — fix them or tick OK / Remove, then Save — before approval.")


# ---------------- Reply & Clarify ----------------

def page_reply() -> None:
    st.header("Reply & Clarify")
    st.caption("Spoken reply to the retailer with Gnani Timbre v2.5 (WhatsApp mocked: download the .ogg voice note).")
    names = retailer_names()
    orders = db.rows(conn, "SELECT * FROM orders ORDER BY id DESC LIMIT 100")
    if not orders:
        st.info("No orders yet.")
        return
    ids = [o["id"] for o in orders]
    cur = st.session_state.get("order_id", ids[0])
    order_id = st.selectbox("Order", ids, index=ids.index(cur) if cur in ids else 0,
                            format_func=lambda i: f"#{i} — {names.get(db.row(conn, 'orders', i)['retailer_id'])} "
                                                  f"({db.row(conn, 'orders', i)['status']})")
    st.session_state.order_id = order_id
    include_total = st.checkbox("Include estimated total (MRP)", value=False)
    draft = reply.draft_reply(conn, order_id, include_total)

    kind = "Clarifying question" if draft.kind == "clarification" else "Order confirmation"
    st.subheader(kind)
    if draft.lang_fallback:
        st.caption("Detected language not supported for replies — replying in Hinglish (fallback recorded).")
    st.info(draft.text)
    if draft.kind == "confirmation" and db.row(conn, "orders", order_id)["status"] == "DRAFT":
        st.caption("Tip: approve the order on the Review page before sending the final confirmation.")
    if st.button("🔊 Generate spoken reply", type="primary"):
        try:
            with st.spinner("Synthesising with Timbre v2.5…"):
                reply.speak(conn, order_id, draft)
        except Exception as exc:  # API / credit-guard / ffmpeg errors: show, don't crash
            st.error(f"Could not generate speech: {exc}")

    sent = db.rows(conn, "SELECT * FROM replies WHERE order_id = ? ORDER BY id DESC", (order_id,))
    for r in sent:
        with st.container(border=True):
            st.markdown(f"**{r['kind'].title()}** · {r['lang']}{' (fallback)' if r['lang_fallback'] else ''} · "
                        f"{r['created_at'].replace('T', ' ')}")
            st.write(r["text"])
            if r["wav_path"] and Path(r["wav_path"]).exists():
                st.audio(r["wav_path"], format="audio/wav")
            if r["ogg_path"] and Path(r["ogg_path"]).exists():
                st.download_button("Download WhatsApp voice note (.ogg)", Path(r["ogg_path"]).read_bytes(),
                                   file_name=f"parchi_reply_{order_id}_{r['id']}.ogg", mime="audio/ogg",
                                   key=f"dl{r['id']}")

    st.subheader("Retailer's answer")
    askable = [(ln, q) for ln in db.order_lines(conn, order_id) if (q := reply.question_for(ln, draft.lang, cat))]
    if not askable:
        st.success("No open questions on this order.")
        return
    for ln, q in askable:
        with st.container(border=True):
            st.markdown(f"**Line {ln['line_no']}** — heard *{ln['product_text']}* — asked: “{q}”")
            f = st.file_uploader("Retailer's reply note", type=AUDIO_TYPES, key=f"clar{ln['id']}")
            if f is not None and st.button("Resolve with this reply", key=f"res{ln['id']}"):
                try:
                    m = pipeline.process_clarification(conn, ln["id"], save_upload(f))
                except Exception as exc:
                    st.error(f"Could not process reply: {exc}")
                    continue
                if m is None:
                    st.error(f"Transcription failed — {pipeline.STT_FAILED_MSG}.")
                elif m.status == "GREEN":
                    st.success(f"Resolved → {sku_label(m.sku_id)} · {m.qty:g} {m.unit}")
                else:
                    st.warning(f"Still unclear ({', '.join(m.reasons)}). Please listen and fix it on the Review page.")
                note = db.rows(conn, "SELECT transcript FROM voice_notes WHERE line_id = ? ORDER BY id DESC LIMIT 1",
                               (ln["id"],))
                if note and note[0]["transcript"]:
                    st.caption(f"Heard: {note[0]['transcript']}")


# ---------------- Export ----------------

def mark_exported(order_id: int) -> None:
    db.update(conn, "orders", order_id, status="EXPORTED", exported_at=db.now())
    db.log_event(conn, "order_exported", order_id)


def page_export() -> None:
    st.header("Export")
    st.caption("Tally is mocked: download a Tally-friendly CSV (one row per line, billed in the SKU's base unit).")
    names = retailer_names()
    orders = db.rows(conn, "SELECT * FROM orders WHERE status IN ('APPROVED', 'EXPORTED') ORDER BY id DESC")
    if not orders:
        st.info("No approved orders yet. Approve an order on the Review page first.")
        return
    ids = [o["id"] for o in orders]
    cur = st.session_state.get("order_id")
    order_id = st.selectbox("Approved order", ids, index=ids.index(cur) if cur in ids else 0,
                            format_func=lambda i: f"#{i} — {names.get(db.row(conn, 'orders', i)['retailer_id'])} "
                                                  f"({db.row(conn, 'orders', i)['status']})")
    try:
        data = export_csv(conn, order_id)
    except ValueError as exc:
        st.error(str(exc))
        return
    df = pd.read_csv(io.StringIO(data), dtype=str)
    st.dataframe(df, hide_index=True)
    st.metric("Order value (MRP, approx.)", f"₹{df['Amount'].astype(float).sum():,.2f}")
    st.download_button("Download CSV", data.encode("utf-8-sig"), file_name=f"parchi_SO-{order_id:05d}.csv",
                       mime="text/csv", type="primary", on_click=mark_exported, args=(order_id,))


# ---------------- Evaluation ----------------

def page_eval() -> None:
    from parchi.eval import channel, report, run

    st.header("Evaluation")
    st.caption("Line-level accuracy of Parchi on the test set: Prisma v3.0 without (A) vs with (B) catalog keywords + "
               "topic, on clean vs phone-degraded / noisy audio. Re-runs reuse the STT cache.")
    with st.expander("Run evaluation"):
        conds, skipped = channel.available_conditions(list(channel.CONDITIONS))
        if skipped:
            st.caption(f"Noise conditions unavailable (add .wav files to data/noise/): {', '.join(skipped)}")
        chosen = st.multiselect("Conditions", conds, default=conds)
        if st.button("Estimate cost"):
            jobs, _ = run.plan(chosen, ["A", "B"])
            st.session_state.eval_est = (tuple(chosen), len(jobs), run.estimate_calls(jobs))
        est = st.session_state.get("eval_est")
        if est and est[0] == tuple(chosen):
            _, n_jobs, n_calls = est
            st.write(f"{n_jobs} jobs · **{n_calls} new API call(s)** needed")
            if n_calls > 100:
                st.warning("More than 100 new calls — run it from the CLI with --yes.")
            elif st.button("Run", type="primary", disabled=n_jobs == 0):
                jobs, _ = run.plan(chosen, ["A", "B"])
                bar = st.progress(0.0)
                before = gnani_client.usage()["total"]
                rows = run.run_jobs(jobs, progress=lambda i, n: bar.progress(i / n))
                d = run.new_run_dir()
                run.write_rows(d, rows, {"conditions": chosen, "skipped_conditions": skipped,
                                         "configs": list(run.CONFIGS.values()),
                                         "api_calls": gnani_client.usage()["total"] - before})
                report.build(d)
                st.session_state.pop("eval_est", None)
                st.rerun()

    d = run.latest_run()
    if d is None or not (d / "report.md").exists():
        st.info("No evaluation report yet. Run `python -m parchi.eval run` (or use the panel above).")
        return
    st.caption(f"Latest report: `{d.relative_to(config.ROOT)}`")
    res = pd.read_csv(d / "results.csv")
    keep = ["source", "condition", "config", "notes", "gold_lines", "f1", "precision", "recall", "sku_accuracy",
            "qty_accuracy", "unsafe_auto_approve_rate", "clarification_rate"]
    st.dataframe(res[keep].round(3), hide_index=True)
    for png in ("f1_by_condition.png", "unsafe_auto_approve.png"):
        if (d / png).exists():
            st.image(str(d / png))
    md = (d / "report.md").read_text(encoding="utf-8")
    st.markdown("\n".join(ln for ln in md.splitlines() if not ln.startswith("![")))  # charts shown above


st.sidebar.title("🧾 Parchi")
st.sidebar.caption("Voice-note order desk · Gnani Prisma v3.0 + Timbre v2.5")
st.sidebar.radio("Page", PAGES, key="page", label_visibility="collapsed")
usage_caption()
env_warnings()
try:
    {"Inbox": page_inbox, "Review": page_review, "Reply & Clarify": page_reply,
     "Export": page_export, "Evaluation": page_eval}[st.session_state.page]()
except Exception as exc:  # last-resort guard: show the error, keep the app alive
    st.error(f"Something went wrong: {exc}")
