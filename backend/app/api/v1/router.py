from fastapi import APIRouter

from app.api.v1 import admin, auth, me

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth.router)
api_v1_router.include_router(me.router)
api_v1_router.include_router(admin.router)
