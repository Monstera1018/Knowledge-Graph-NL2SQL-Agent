from contextlib import asynccontextmanager

from fastapi import FastAPI

from tools.graph import close_graph_driver
from tools.vector import close_client
from tools.db import close_sql_engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_graph_driver()
    await close_client()
    await close_sql_engine()
