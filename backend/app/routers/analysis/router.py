from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.schemas.analysis import AnalysisRequest
from app.schemas.company import Company, CompanyListResponse
from app.schemas.errors import STATUS_HTTP_CODE, ErrorResponse
from app.schemas.user import CurrentUser
from app.services.analysis import companies
from app.services.analysis.service import run_analysis
from app.services.auth.service import get_optional_user

router = APIRouter(tags=["analysis"])


@router.get("/companies", response_model=CompanyListResponse)
def list_companies() -> CompanyListResponse:
    return CompanyListResponse(
        status="success",
        snapshot_date=companies.snapshot_date(),
        companies=[Company(**c) for c in companies.list_companies()],
    )


@router.post("/analyses")
async def create_analysis(
    body: AnalysisRequest, current_user: CurrentUser | None = Depends(get_optional_user)
):
    result = await run_analysis(body.query, current_user)
    if isinstance(result, ErrorResponse):
        status_code = STATUS_HTTP_CODE.get(result.status, 500)
        return JSONResponse(status_code=status_code, content=result.model_dump())
    return result
