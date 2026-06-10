import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT.joinpath(".env"))

from google.adk.agents import Agent

from adk_agents.common import RetrievalExtractionResult, get_llm_model
from adk_agents.extractor.prompts import EXTRACTOR_INSTRUCTION

root_agent = Agent(
    model=get_llm_model(),
    name="extractor",
    description="从用户输入与历史对话中提取用于语义检索的关键内容。",
    instruction=EXTRACTOR_INSTRUCTION,
)
