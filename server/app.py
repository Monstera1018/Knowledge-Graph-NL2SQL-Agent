import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.lifespan import lifespan
from server.routers import register_routers

_WEB_DIST = _PROJECT_ROOT / "web" / "dist" / "web" / "browser"


def _accepts_html(scope: dict) -> bool:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    return "text/html" in headers.get("accept", "")


class SpaStaticFiles(StaticFiles):
    """提供 Angular 静态资源，并在浏览器路由刷新时回退到 index.html。"""

    async def get_response(self, path: str, scope: dict):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if (
                exc.status_code != 404
                or scope.get("method") not in {"GET", "HEAD"}
                or path == "api"
                or path.startswith("api/")
                or not _accepts_html(scope)
            ):
                raise
            return await super().get_response("index.html", scope)

app = FastAPI(
    title="nl2sql-agent",
    description="Knowledge-graph NL2SQL agent（自然语言问数助手）",
    lifespan=lifespan,
)

_cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_routers(app)

# 注册 API 路由后再挂载前端；前端浏览器路由刷新时回退到 index.html。
if _WEB_DIST.is_dir():
    app.mount("/", SpaStaticFiles(directory=_WEB_DIST, html=True), name="frontend")
