from fastapi import APIRouter

api_v1_router = APIRouter(prefix="/api/v1")

# Future sprints register their routers here, e.g.:
# api_v1_router.include_router(auth.router)
# api_v1_router.include_router(nodes.router)
