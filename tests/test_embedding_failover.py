import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.embedding import get_embeddings
from tools.llm import clear_profile_rate_limits


class EmbeddingFailoverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_profile_rate_limits()

    async def test_switches_to_agent_plan_after_quota(self) -> None:
        env = {
            "EMBEDDING_MODEL_NAME": "openai:doubao-embedding-vision",
            "EMBEDDING_API_BASE": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "EMBEDDING_API_KEY": "coding-key",
            "EMBEDDING_MODE": "litellm",
            "LLM_FALLBACK_API_BASE": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "LLM_FALLBACK_API_KEY": "agent-plan-key",
            "DEV_CACHE": "false",
        }
        ok = SimpleNamespace(data=[{"embedding": [0.1, 0.2]}])
        mock_embed = AsyncMock(
            side_effect=[
                Exception("You have exceeded the 5-hour usage quota"),
                ok,
            ]
        )
        with patch.dict(os.environ, env, clear=False), patch(
            "tools.embedding.aembedding", mock_embed
        ), patch("tools.embedding.asyncio.sleep", new=AsyncMock()):
            vectors = await get_embeddings(["杭州欠费用户"])
        self.assertEqual(vectors, [[0.1, 0.2]])
        self.assertEqual(mock_embed.call_count, 2)
        self.assertEqual(
            mock_embed.call_args_list[0].kwargs["api_base"],
            "https://ark.cn-beijing.volces.com/api/coding/v3",
        )
        self.assertEqual(
            mock_embed.call_args_list[1].kwargs["api_base"],
            "https://ark.cn-beijing.volces.com/api/plan/v3",
        )
        self.assertEqual(mock_embed.call_args_list[1].kwargs["api_key"], "agent-plan-key")

    async def test_does_not_switch_when_credentials_are_explicit(self) -> None:
        mock_embed = AsyncMock(side_effect=Exception("You have exceeded the 5-hour usage quota"))
        with patch("tools.embedding.aembedding", mock_embed), patch.dict(
            os.environ,
            {
                "EMBEDDING_MODE": "litellm",
                "LLM_FALLBACK_API_BASE": "https://ark.cn-beijing.volces.com/api/plan/v3",
                "LLM_FALLBACK_API_KEY": "agent-plan-key",
                "DEV_CACHE": "false",
            },
            clear=False,
        ):
            with self.assertRaises(Exception):
                await get_embeddings(
                    ["杭州欠费用户"],
                    model="openai:doubao-embedding-vision",
                    api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
                    api_key="coding-key",
                )
        self.assertEqual(mock_embed.call_count, 1)


if __name__ == "__main__":
    unittest.main()
