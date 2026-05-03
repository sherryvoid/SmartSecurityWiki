from fastapi import APIRouter, Depends

from app.core.security import require_user
from app.services.model_health import models_health

router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(require_user)])


@router.get("/health")
async def health() -> dict:
    return await models_health()
