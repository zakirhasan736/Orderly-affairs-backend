# app/api/ai_autofill_routes.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl
from app.ai.extractors.section1_extractor import extract_section1_from_document

router = APIRouter(prefix="/ai", tags=["ai-autofill"])

class AutofillSectionRequest(BaseModel):
    section: str
    document_url: HttpUrl
    subsection: str | None = None
    mime_type: str | None = "application/pdf"

@router.post("/autofill-section")
async def autofill_section(payload: AutofillSectionRequest):
    try:
        if payload.section != "vital_information":
            raise HTTPException(status_code=400, detail="Unsupported section for now")

        result = await extract_section1_from_document(
            document_url=str(payload.document_url),
            subsection=payload.subsection,
            mime_type=payload.mime_type or "application/pdf",
        )

        return {
            "success": True,
            "section": payload.section,
            "scope": "subsection" if payload.subsection else "section",
            "subsection": payload.subsection,
            "result": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI autofill failed: {str(e)}")