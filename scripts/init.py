import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT.joinpath(".env"))

from tools.ingest import run_all_imports, IMPORT_JOBS
from tools.graph.internal import close_graph_driver, truncate_graph
from tools.vector import close_client, drop_collection, flush_all_vector_collections


async def main() -> None:
    await truncate_graph()
    await drop_collection()
    await drop_collection(collection_name="TYWS")

    await run_all_imports("default", _PROJECT_ROOT.joinpath("resources", "data"), IMPORT_JOBS
                          )
    await flush_all_vector_collections()

    await close_graph_driver()
    await close_client()


if __name__ == "__main__":
    asyncio.run(main())
