"""v1 总路由：装配全部 15 个路由模块（30 个接口）。"""

from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    assistant,
    auth,
    catalog,
    dashboard,
    events,
    forecasts,
    holidays,
    jobs,
    knowledge,
    scenic,
    sources,
    transport,
    videos,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(sources.router)
api_router.include_router(scenic.router)
api_router.include_router(transport.router)
api_router.include_router(dashboard.router)
api_router.include_router(analytics.router)
api_router.include_router(forecasts.router)
api_router.include_router(holidays.router)
api_router.include_router(events.router)
api_router.include_router(assistant.router)
api_router.include_router(knowledge.router)
api_router.include_router(videos.router)
api_router.include_router(jobs.router)

from app.api.v1 import public_data
api_router.include_router(public_data.router)
