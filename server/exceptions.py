from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tools.graph import ConflictError, NotFoundError


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def handle_not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def handle_conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
