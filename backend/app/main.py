import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api.routes import router
from .config import ROOT_DIR, get_settings
from .services.runtime import scheduler_loop, startup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DIST = ROOT_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    task = None
    if get_settings().enable_scheduler:
        import asyncio

        task = asyncio.create_task(scheduler_loop())
    yield
    if task is not None:
        task.cancel()
        import asyncio

        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    app = FastAPI(title="duck-harness", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8787",
            "http://localhost:8787",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/")
    def index():
        page = DIST / "index.html"
        if page.exists():
            return FileResponse(page)
        return {"service": "duck-harness", "hint": "前端还没构建。先在 frontend 目录执行 npm install 和 npm run build。"}

    @app.get("/{full_path:path}")
    def assets(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(404, "接口不存在")
        target = (DIST / full_path).resolve()
        if not str(target).startswith(str(DIST.resolve())):
            raise HTTPException(404, "找不到页面")
        if target.is_file():
            return FileResponse(target)
        page = DIST / "index.html"
        if page.exists():
            return FileResponse(page)
        raise HTTPException(404, "前端还没构建")

    return app


app = create_app()
