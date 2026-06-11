"""Repo map and intelligence API routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from patchquest.api.schemas import RepoFileInfo, RepoMapResponse
from patchquest.database import get_db

router = APIRouter(tags=["repo"])


@router.get("/api/repo/intelligence-status")
async def intelligence_status():
    from patchquest.memory.tree_sitter_registry import get_status
    return get_status()


@router.get("/api/repo-map", response_model=RepoMapResponse)
async def get_repo_map(repo_path: str = Query(...)) -> RepoMapResponse:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT file_path, language, size FROM repo_files WHERE repo_path = ? ORDER BY file_path",
            (repo_path,),
        ).fetchall()

    files = [
        RepoFileInfo(path=r["file_path"], language=r["language"], size=r["size"])
        for r in rows
    ]
    return RepoMapResponse(repo_path=repo_path, files=files, total_files=len(files))
