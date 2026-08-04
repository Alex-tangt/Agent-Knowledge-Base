import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from config.config import CORS_ORIGINS, API_PREFIX
from api.routes import router
from utils.logger import logger
from services.langsmith_service import langsmith_service
from contextlib import asynccontextmanager


async def warmup_models():
    logger.info("Background warmup started...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _warmup_sync)
    logger.info("Background warmup completed")


def _warmup_sync():
    from api.routes import _get_rag_service
    rag_service = _get_rag_service()
    rag_service.warmup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup")

    if langsmith_service.is_enabled:
        logger.info(f"LangSmith tracing enabled for project: {langsmith_service.client.project if langsmith_service.client else 'N/A'}")
    else:
        logger.info("LangSmith tracing disabled or not configured")

    asyncio.create_task(warmup_models())

    yield
    logger.info("Application shutdown")

app = FastAPI(
    title="AI Chat API",
    description="一个基于FastAPI的AI聊天API",
    version="1.0.0",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册API路由
app.include_router(router, prefix=API_PREFIX)

# 静态文件服务（提供前端界面），路径锚定到 backend/ 所在位置，避免依赖启动目录
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)