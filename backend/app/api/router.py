from fastapi import APIRouter
from app.api.routes.health import router as health_router
from app.api.routes.auth import router as auth_router
from app.api.routes.credits import router as credits_router
from app.api.routes.scene import router as scene_router
from app.api.routes.assets import router as assets_router
from app.api.routes.lookbook import router as lookbook_router
from app.api.routes.video import router as video_router
from app.api.routes.prints import router as prints_router
from app.api.routes.clip import router as clip_router
from app.api.routes.clip_comfy import router as clip_comfy_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(credits_router, tags=["credits"])
api_router.include_router(scene_router, tags=["scene"])
api_router.include_router(assets_router, tags=["assets"])
api_router.include_router(lookbook_router, tags=["lookbook"])
api_router.include_router(video_router, tags=["video"])
api_router.include_router(prints_router, tags=["prints"])
api_router.include_router(clip_router, tags=["clip"])
api_router.include_router(clip_comfy_router, tags=["clip-comfy"])
