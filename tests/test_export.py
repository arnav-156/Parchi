import csv
import io

import pytest

from parchi import db
from parchi.catalog import load_catalog
from parchi.export import export_csv, to_base_qty

CAT = load_catalog()


@pytest.mark.parametrize("sku,qty,unit,base", [
    ("BIS001", 2, "CARTON", 240),   # 120 packets/carton
    ("TP001", 2, "DOZEN", 24),
    ("NOO001", 5, "BOX", 5),
    ("SUG001", 25, "KG", 0.5),      # 50 kg sack
    ("BEV001", 1.5, "CARTON", 36),
])
def test_to_base_qty(sku, qty, unit, base):
    assert to_base_qty(qty, unit, CAT.by_id[sku]) == base


@pytest.fixture
def order(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    oid = db.insert(conn, "orders", retailer_id="R001", status="DRAFT", created_at=db.now())
    nid = db.insert(conn, "voice_notes", order_id=oid, status="PARSED", created_at=db.now(),
                    notes_json=["Colgate pichli baar kam aaya tha"])
    db.insert(conn, "order_lines", order_id=oid, note_id=nid, line_no=1, sku_id="BIS001", qty=2, unit="CARTON",
              status="GREEN")
    amber = db.insert(conn, "order_lines", order_id=oid, note_id=nid, line_no=2, sku_id=None, qty=24, unit="PIECE",
                      status="AMBER", candidates=["TP001", "TP002"])
    yield conn, oid, amber
    conn.close()


def test_export_blocked_until_approved_and_resolved(order):
    conn, oid, amber = order
    with pytest.raises(ValueError):
        export_csv(conn, oid)  # not approved
    with pytest.raises(ValueError):
        db.approve_order(conn, oid)  # AMBER line blocks approval
    db.update(conn, "order_lines", amber, sku_id="TP002", status="GREEN", resolved_by="clerk")
    db.approve_order(conn, oid)
    rows = list(csv.DictReader(io.StringIO(export_csv(conn, oid))))
    assert [(r["Item Code"], r["Billed Qty"], r["Amount"]) for r in rows] == [
        ("BIS001", "240", "1200.00"), ("TP002", "24", "2520.00")]
    assert rows[0]["Party Name"] == "Sharma General Store" and "kam aaya" in rows[0]["Narration"]


def test_removed_lines_are_not_exported(order):
    conn, oid, amber = order
    db.update(conn, "order_lines", amber, removed=1)
    db.approve_order(conn, oid)
    assert [r["Item Code"] for r in csv.DictReader(io.StringIO(export_csv(conn, oid)))] == ["BIS001"]
