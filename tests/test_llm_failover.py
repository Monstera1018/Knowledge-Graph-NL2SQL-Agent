import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.llm import (
    chat,
    clear_profile_rate_limits,
    generate_with_failover,
    is_llm_connection_error,
    is_llm_failover_error,
    is_llm_quota_or_rate_limit_error,
    load_llm_profiles,
)


class _FakeExc(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class _FakeBackend:
    def __init__(self, name: str, model: str, chunks=None, error=None) -> None:
        self.profile_name = name
        self.model = model
        self.chunks = list(chunks or [])
        self.error = error
        self.calls = 0

    async def generate_content_async(self, llm_request, stream: bool = False):
        self.calls += 1
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk


class QuotaErrorDetectTest(unittest.TestCase):
    def test_burst_message_is_quota(self):
        exc = Exception("System protection triggered by request burst")
        self.assertTrue(is_llm_quota_or_rate_limit_error(exc))

    def test_chinese_burst_message_is_quota(self):
        self.assertTrue(is_llm_quota_or_rate_limit_error(Exception("触发突发保护，请降低访问频率")))

    def test_slow_down_message_is_quota(self):
        self.assertTrue(
            is_llm_quota_or_rate_limit_error(Exception("Please slow down traffic"))
        )

    def test_http_429_is_quota(self):
        self.assertTrue(is_llm_quota_or_rate_limit_error(_FakeExc("limited", 429)))

    def test_auth_error_is_not_quota(self):
        self.assertFalse(
            is_llm_quota_or_rate_limit_error(_FakeExc("invalid api key", 401))
        )

    def test_generic_server_error_is_not_quota(self):
        self.assertFalse(is_llm_quota_or_rate_limit_error(Exception("internal error")))
        self.assertFalse(is_llm_failover_error(Exception("internal error")))

    def test_connection_error_is_failover(self):
        exc = Exception("litellm.InternalServerError: OpenAIException - Connection error.")
        self.assertTrue(is_llm_connection_error(exc))
        self.assertTrue(is_llm_failover_error(exc))
        self.assertFalse(is_llm_quota_or_rate_limit_error(exc))


class LoadProfilesTest(unittest.TestCase):
    def test_primary_only_when_fallback_incomplete(self):
        env = {
            "LLM_MODEL_NAME": "openai:deepseek-v4-flash",
            "OPENAI_API_BASE": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "OPENAI_API_KEY": "coding-key",
            "LLM_FALLBACK_API_BASE": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "LLM_FALLBACK_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=False):
            profiles = load_llm_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "primary")

    def test_loads_agent_plan_fallback(self):
        env = {
            "LLM_MODEL_NAME": "openai:deepseek-v4-flash",
            "OPENAI_API_BASE": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "OPENAI_API_KEY": "coding-key",
            "LLM_FALLBACK_MODEL_NAME": "openai:ark-code-latest",
            "LLM_FALLBACK_API_BASE": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "LLM_FALLBACK_API_KEY": "agent-plan-key",
        }
        with patch.dict(os.environ, env, clear=False):
            profiles = load_llm_profiles()
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[1].name, "fallback")
        self.assertEqual(profiles[1].api_base, "https://ark.cn-beijing.volces.com/api/plan/v3")
        self.assertEqual(profiles[1].model, "openai:ark-code-latest")


class GenerateFailoverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_profile_rate_limits()

    async def test_switches_after_burst_before_any_chunk(self):
        primary = _FakeBackend(
            "primary",
            "openai/deepseek-v4-flash",
            error=Exception("System protection triggered by request burst"),
        )
        fallback = _FakeBackend("fallback", "openai/ark-code-latest", chunks=["ok"])
        with patch("tools.llm.asyncio.sleep", new=AsyncMock()):
            chunks = [
                item
                async for item in generate_with_failover(
                    [primary, fallback], SimpleNamespace(model=primary.model)
                )
            ]
        self.assertEqual(chunks, ["ok"])
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)

    async def test_next_call_skips_recently_limited_primary(self):
        first_primary = _FakeBackend(
            "primary",
            "openai/ark-code-latest",
            error=Exception("System protection triggered by request burst"),
        )
        first_fallback = _FakeBackend("fallback", "openai/deepseek-v4-flash", chunks=["ok"])
        with patch("tools.llm.asyncio.sleep", new=AsyncMock()):
            async for _ in generate_with_failover(
                [first_primary, first_fallback], SimpleNamespace(model=first_primary.model)
            ):
                pass

        later_primary = _FakeBackend("primary", "openai/ark-code-latest", chunks=["should-not-use"])
        later_fallback = _FakeBackend("fallback", "openai/deepseek-v4-flash", chunks=["still-ok"])
        chunks = [
            item
            async for item in generate_with_failover(
                [later_primary, later_fallback], SimpleNamespace(model=later_primary.model)
            )
        ]
        self.assertEqual(chunks, ["still-ok"])
        self.assertEqual(later_primary.calls, 0)
        self.assertEqual(later_fallback.calls, 1)

    async def test_does_not_switch_after_partial_output(self):
        class _PartialBackend:
            profile_name = "primary"
            model = "openai/deepseek-v4-flash"

            async def generate_content_async(self, llm_request, stream: bool = False):
                yield "partial"
                raise Exception("System protection triggered by request burst")

        fallback = _FakeBackend("fallback", "openai/ark-code-latest", chunks=["ok"])
        with self.assertRaises(Exception) as ctx:
            async for _ in generate_with_failover(
                [_PartialBackend(), fallback], SimpleNamespace(model="openai/deepseek-v4-flash")
            ):
                pass
        self.assertIn("request burst", str(ctx.exception))
        self.assertEqual(fallback.calls, 0)

    async def test_switches_after_empty_stream_chunk(self):
        class _EmptyThenError:
            profile_name = "primary"
            model = "openai/deepseek-v4-flash"

            async def generate_content_async(self, llm_request, stream: bool = False):
                yield SimpleNamespace(
                    content=SimpleNamespace(parts=[SimpleNamespace(text="")]),
                    partial=True,
                    error_message=None,
                    error_code=None,
                )
                raise Exception("System protection triggered by request burst")

        fallback = _FakeBackend("fallback", "openai/ark-code-latest", chunks=["ok"])
        with patch("tools.llm.asyncio.sleep", new=AsyncMock()):
            chunks = [
                item
                async for item in generate_with_failover(
                    [_EmptyThenError(), fallback],
                    SimpleNamespace(model="openai/deepseek-v4-flash"),
                )
            ]
        self.assertEqual(chunks, ["ok"])
        self.assertEqual(fallback.calls, 1)

    async def test_switches_on_connection_error(self):
        primary = _FakeBackend(
            "primary",
            "openai/ark-code-latest",
            error=Exception("InternalServerError: OpenAIException - Connection error."),
        )
        fallback = _FakeBackend("fallback", "openai/deepseek-v4-flash", chunks=["ok"])
        with patch("tools.llm.asyncio.sleep", new=AsyncMock()):
            chunks = [
                item
                async for item in generate_with_failover(
                    [primary, fallback], SimpleNamespace(model=primary.model)
                )
            ]
        self.assertEqual(chunks, ["ok"])
        self.assertEqual(fallback.calls, 1)

    async def test_does_not_switch_on_auth_error(self):
        primary = _FakeBackend(
            "primary",
            "openai/deepseek-v4-flash",
            error=_FakeExc("invalid api key", 401),
        )
        fallback = _FakeBackend("fallback", "openai/ark-code-latest", chunks=["ok"])
        with self.assertRaises(_FakeExc):
            async for _ in generate_with_failover(
                [primary, fallback], SimpleNamespace(model=primary.model)
            ):
                pass
        self.assertEqual(fallback.calls, 0)


class ChatFailoverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_profile_rate_limits()

    async def test_chat_retries_on_agent_plan_after_burst(self):
        env = {
            "LLM_MODEL_NAME": "openai:deepseek-v4-flash",
            "OPENAI_API_BASE": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "OPENAI_API_KEY": "coding-key",
            "LLM_FALLBACK_MODEL_NAME": "openai:ark-code-latest",
            "LLM_FALLBACK_API_BASE": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "LLM_FALLBACK_API_KEY": "agent-plan-key",
            "DEV_CACHE": "false",
        }
        ok = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="来自备用套餐"))]
        )
        mock_ac = AsyncMock(
            side_effect=[Exception("System protection triggered by request burst"), ok]
        )
        with patch.dict(os.environ, env, clear=False), patch(
            "tools.llm.acompletion", mock_ac
        ), patch("tools.llm.asyncio.sleep", new=AsyncMock()):
            result = await chat("生成分析报告")
        self.assertEqual(result, "来自备用套餐")
        self.assertEqual(mock_ac.call_count, 2)
        self.assertEqual(
            mock_ac.call_args_list[0].kwargs["api_base"],
            "https://ark.cn-beijing.volces.com/api/coding/v3",
        )
        self.assertEqual(
            mock_ac.call_args_list[1].kwargs["api_base"],
            "https://ark.cn-beijing.volces.com/api/plan/v3",
        )
        self.assertEqual(mock_ac.call_args_list[1].kwargs["api_key"], "agent-plan-key")


if __name__ == "__main__":
    unittest.main()
