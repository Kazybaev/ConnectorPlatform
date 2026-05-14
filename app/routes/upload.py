from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.models.schemas import AgentKnowledgeBase
from app.services.excel_parser import ExcelParserError, ExcelParserService, get_excel_parser_service

router = APIRouter(tags=["upload"])

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"}


@router.post("/upload", response_model=AgentKnowledgeBase)
async def upload_excel(
    file: UploadFile = File(...),
    parser_service: ExcelParserService = Depends(get_excel_parser_service),
) -> AgentKnowledgeBase:
    """Parse an uploaded Excel file into the agent knowledge JSON schema."""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()

    if suffix and suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Please upload an Excel file.",
        )

    content = await file.read()
    await file.close()

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    try:
        return parser_service.parse(content)
    except ExcelParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
