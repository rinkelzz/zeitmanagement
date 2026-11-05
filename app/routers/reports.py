"""Auswertung der Projektzeiten und Export als PDF, Excel und CSV."""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import timereport
from ..access import sees_all
from ..db import audit, get_db
from ..security import current_user

router = APIRouter(prefix="/api/v1/reports", tags=["Auswertung"])


def _build(conn, user, d_from, d_to, customer_id, project_id, user_id, group):
    try:
        return timereport.build(conn, user, d_from, d_to, customer_id, project_id, user_id, group)
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
    entries: bool = Query(True, description="Einzelbuchungen mitliefern"),
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    data = _build(conn, user, from_, to, customer_id, project_id, user_id, group)
    data["created"] = data["created"].isoformat()
    if not entries:
        data["entries"] = []
    boss_team = data["team"] and sees_all(conn, user)
    data["group_options"] = {k: v for k, v in timereport.GROUPS.items() if k != "user" or boss_team}
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
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    data = _build(conn, user, from_, to, customer_id, project_id, user_id, group)
    try:
        content, mime, name = timereport.render(conn, data, format)
    except timereport.ReportError as e:
        raise HTTPException(400, str(e))
    audit(conn, user["id"], "report_export", "report",
          f"{format.upper()} {timereport.period_text(data)}" + (" · " + ", ".join(data["filters"]) if data["filters"] else ""))
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                       "Cache-Control": "no-store"})
