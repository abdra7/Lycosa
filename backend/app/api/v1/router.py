from fastapi import APIRouter

from app.api.v1 import admin, auth, knowledge, me, nodes, recommendations, tasks, workflows

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth.router)
api_v1_router.include_router(me.router)
api_v1_router.include_router(admin.router)
api_v1_router.include_router(nodes.router)
api_v1_router.include_router(recommendations.router)
api_v1_router.include_router(tasks.router)
api_v1_router.include_router(knowledge.router)
api_v1_router.include_router(workflows.router)
