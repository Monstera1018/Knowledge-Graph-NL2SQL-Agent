from fastapi import FastAPI

from server.exceptions import register_exception_handlers
from server.routers.auth import router as auth_router
from server.routers.chat import router as chat_router
from server.routers.graph import graph_router
from server.routers.health import router as health_router
from server.routers.ingest import router as import_router
from server.routers.metadata import (
    columns_router,
    enums_router,
    join_relations_router,
    knowledge_router,
    sqls_router,
    tables_router,
)


def register_routers(app: FastAPI) -> None:
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(import_router)
    app.include_router(tables_router)
    app.include_router(columns_router)
    app.include_router(enums_router)
    app.include_router(join_relations_router)
    app.include_router(knowledge_router)
    app.include_router(sqls_router)
    app.include_router(graph_router)
