"""Original public statistical periods and actual observation availability."""
from fastapi import APIRouter, Depends, Request
from app.core.permissions import require_authenticated
from app.schemas.common import envelope
from app.services.public_data import entity_data

router = APIRouter(prefix="/public-data", tags=["公开统计资料"])


@router.get("/{entity_id}/series")
def get_series(entity_id: str, request: Request, metric: str, grain: str = 'auto', preferred_grain: str = 'month',
               start: str | None = None, end: str | None = None, _session=Depends(require_authenticated)):
    from app.services.native_series import query_series
    return envelope(request, query_series(entity_id,metric,grain,preferred_grain,start,end))


@router.get("/{entity_id}")
def get_public_data(entity_id: str, request: Request, include_pending: bool = False,
                    _session=Depends(require_authenticated)):
    return envelope(request, entity_data(entity_id, include_pending))
