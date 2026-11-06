"""Approved order → Tally-friendly CSV (one row per stock item line)."""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date

from parchi import db
from parchi.catalog import Catalog, Sku, load_catalog, pack_amount

COLUMNS = [
    "Voucher Date", "Voucher Type", "Voucher No", "Party Name", "Item Code", "Item Name",
    "Ordered Qty", "Ordered Unit", "Billed Qty", "Base Unit", "Rate", "Amount", "Narration",
]
_MASS_VOL = {"G": ("mass", 1), "KG": ("mass", 1000), "ML": ("vol", 1), "L": ("vol", 1000)}


def to_base_qty(qty: float, unit: str, sku: Sku) -> float:
    """Ordered qty/unit → qty in the SKU's sale unit (cartons, dozens, kilos converted).
    BOX/PACKET/PIECE/STRIP count as one sale unit each ("Maggi paanch dabba" = 5 packets)."""
    if unit == "CARTON":
        return qty * sku.units_per_carton
    if unit == "DOZEN":
        return qty * 12
    if unit in _MASS_VOL and (pa := pack_amount(sku.pack_size)) and pa[1] == _MASS_VOL[unit][0]:
        return qty * _MASS_VOL[unit][1] / pa[0]
    return qty


def export_rows(conn: sqlite3.Connection, order_id: int, cat: Catalog | None = None) -> list[dict[str, object]]:
    cat = cat or load_catalog()
    order = db.row(conn, "orders", order_id)
    if order is None or order["status"] not in ("APPROVED", "EXPORTED"):
        raise ValueError("Only approved orders can be exported.")
    if db.unresolved_lines(conn, order_id):
        raise ValueError("Order has unresolved AMBER/RED lines.")
    retailer = db.row(conn, "retailers", order["retailer_id"])
    notes = db.rows(conn, "SELECT notes_json FROM voice_notes WHERE order_id = ? AND kind = 'order'", (order_id,))
    narration = "; ".join(n for r in notes for n in (r["notes_json"] or []))
    out = []
    for ln in db.order_lines(conn, order_id):
        if ln["removed"]:
            continue
        sku = cat.by_id[ln["sku_id"]]
        billed = to_base_qty(ln["qty"], ln["unit"], sku)
        out.append({
            "Voucher Date": (order["approved_at"] or "")[:10] or date.today().isoformat(),
            "Voucher Type": "Sales Order",
            "Voucher No": f"SO-{order_id:05d}",
            "Party Name": retailer["name"] if retailer else order["retailer_id"],
            "Item Code": sku.sku_id,
            "Item Name": sku.label,
            "Ordered Qty": f"{ln['qty']:g}",
            "Ordered Unit": ln["unit"],
            "Billed Qty": f"{billed:g}",
            "Base Unit": sku.sale_unit,
            "Rate": f"{sku.mrp_inr:.2f}",
            "Amount": f"{billed * sku.mrp_inr:.2f}",
            "Narration": narration,
        })
    return out


def export_csv(conn: sqlite3.Connection, order_id: int) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(export_rows(conn, order_id))
    return buf.getvalue()
