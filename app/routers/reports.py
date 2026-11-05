"""Auswertung der Projektzeiten und Export als PDF, Excel und CSV."""
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import timereport
from ..access import sees_all
from ..db import audit, get_db
from ..security import admin_user, current_user
from ..timeutil import now_utc_str

router = APIRouter(prefix="/api/v1/reports", tags=["Auswertung"])


def _build(conn, user, d_from, d_to, customer_id, project_id, user_id, group, billed="all", category_id=None):
    from .. import modules
    if group == "ticket" and not modules.enabled(conn, "helpdesk"):
        group = "customer"
    try:
        return timereport.build(conn, user, d_from, d_to, customer_id, project_id, user_id, group, billed, category_id)
    except timereport.ReportError as e:
        raise HTTPException(400, str(e))


@router.get("/times")
def report(
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    customer_id: Optional[int] = Query(None, description="0 = Projekte ohne Kunde"),
    project_id: Optional[int] = None,
    user_id: Optional[int] = Query(None, description="nur Chef; Mitarbeiter sehen immer nur ihre eigenen Zeiten"),
    group: str = Query("customer", description=", ".join(timereport.GROUPS)),
    billed: str = Query("all", description="all, open (nicht abgerechnet) oder billed"),
    category_id: Optional[int] = Query(None, description="nur Zeiten an Tickets dieser Kategorie"),
    entries: bool = Query(True, description="Einzelbuchungen mitliefern"),
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    from .. import modules
    data = _build(conn, user, from_, to, customer_id, project_id, user_id, group, billed, category_id)
    data["created"] = data["created"].isoformat()
    if not entries:
        data["entries"] = []
    boss_team = data["team"] and sees_all(conn, user)
    hd_on = modules.enabled(conn, "helpdesk")
    data["group_options"] = {k: v for k, v in timereport.GROUPS.items() if (k != "user" or boss_team) and (k != "ticket" or hd_on)}
    data["can_bill"] = sees_all(conn, user)
    return data


@router.get("/times/export")
def export(
    format: str = Query(..., description="pdf, xlsx oder csv"),
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    customer_id: Optional[int] = None,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    group: str = "customer",
    billed: str = "all",
    category_id: Optional[int] = None,
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    data = _build(conn, user, from_, to, customer_id, project_id, user_id, group, billed, category_id)
    try:
        content, mime, name = timereport.render(conn, data, format)
    except timereport.ReportError as e:
        raise HTTPException(400, str(e))
    audit(conn, user["id"], "report_export", "report",
          f"{format.upper()} {timereport.period_text(data)}" + (" · " + ", ".join(data["filters"]) if data["filters"] else ""))
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                       "Cache-Control": "no-store"})


class BillingIn(BaseModel):
    action: str = Field(..., description="bill (als abgerechnet markieren) oder unbill (zurücknehmen)")
    ref: str = Field("", max_length=100, description="Vermerk, z. B. Rechnungsnummer")
    from_: date = Field(..., alias="from")
    to: date
    customer_id: Optional[int] = None
    project_id: Optional[int] = None
    user_id: Optional[int] = None
    entry_ids: Optional[List[int]] = Field(None, max_length=5000, description="nur diese Buchungen (innerhalb des Filters)")


@router.post("/times/billing")
def billing(data: BillingIn, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Alle passenden, abgeschlossenen Buchungen als abgerechnet markieren – oder die Markierung zurücknehmen."""
    if data.action not in ("bill", "unbill"):
        raise HTTPException(400, "Aktion muss bill oder unbill sein")
    try:
        where, args, _ = timereport.where_clause(conn, user, data.from_, data.to, data.customer_id, data.project_id,
                                                 data.user_id, "open" if data.action == "bill" else "billed")
    except timereport.ReportError as e:
        raise HTTPException(400, str(e))
    if data.entry_ids is not None:
        if not data.entry_ids:
            raise HTTPException(400, "Keine Buchungen ausgewählt")
        where.append(f"e.id IN ({','.join('?' * len(data.entry_ids))})")
        args += list(data.entry_ids)
    rows = conn.execute("SELECT e.id, ROUND((julianday(e.end_utc) - julianday(e.start_utc)) * 1440) AS m "
                        "FROM time_entries e JOIN projects p ON p.id = e.project_id WHERE " + " AND ".join(where), args).fetchall()
    ids = [r["id"] for r in rows]
    minutes = int(sum(r["m"] for r in rows))
    if ids:
        marks = ",".join("?" * len(ids))
        if data.action == "bill":
            conn.execute(f"UPDATE time_entries SET billed_at = ?, billed_by = ?, billed_ref = ? WHERE id IN ({marks})",
                         [now_utc_str(), user["id"], data.ref.strip() or None] + ids)
        else:
            conn.execute(f"UPDATE time_entries SET billed_at = NULL, billed_by = NULL, billed_ref = NULL WHERE id IN ({marks})", ids)
        audit(conn, user["id"], "time_billed" if data.action == "bill" else "time_unbilled", "report",
              f"{len(ids)} Buchungen, {minutes // 60}:{minutes % 60:02d} h · {data.from_:%d.%m.%Y}–{data.to:%d.%m.%Y}"
              + (f" · {data.ref.strip()}" if data.ref.strip() else ""))
    return {"count": len(ids), "minutes": minutes}
