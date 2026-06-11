"""Reports API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from patchquest.api.schemas import ReportResponse
from patchquest.database import get_db, now_iso

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{run_id}", response_model=ReportResponse)
async def get_report(run_id: str) -> ReportResponse:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM reports WHERE run_id = ?", (run_id,)).fetchone()
    if r:
        return ReportResponse(
            run_id=r["run_id"],
            report_md=r["report_md"],
            diff_patch=r["diff_patch"],
            commands_log=r["commands_log"],
            created_at=r["created_at"],
        )

    with get_db() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not run:
        raise HTTPException(404, "Run not found")

    if run["status"] in ("failed", "completed"):
        from patchquest.reports.final_report import generate_report_from_run_record
        report = generate_report_from_run_record(run_id)
        return ReportResponse(
            run_id=run_id,
            report_md=report["report_md"],
            diff_patch=report.get("diff_patch"),
            commands_log=report.get("commands_log"),
            created_at=now_iso(),
        )

    raise HTTPException(404, "Report not available yet — run is still in progress")
