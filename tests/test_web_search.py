"""Tests for the Ollama web_search and web_fetch tool wrappers.

The wrappers live in :mod:`grc_agent.runtime.web_search`. They hit
Ollama's hosted web-search and web-fetch REST APIs and return
results in the same shape as the other runtime tools (an
ok/result/error dict).

The tests here focus on:
- the request payload matches the API contract
- the response is unpacked into the standard ``_tool_result`` shape
- network / auth errors surface as a typed ``ok=False`` payload
  with a clear error_type, NOT a Python exception

A real network call is not required — the tests monkeypatch
``httpx.post`` to return canned responses.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class WebSearchApiContractTests(unittest.TestCase):
    """The wrapper must build the exact request the Ollama API expects."""

    def test_web_search_posts_query_to_ollama_endpoint(self) -> None:
        from grc_agent.runtime import web_search

        fake_response = mock.MagicMock()
        fake_response.raise_for_status = mock.MagicMock()
        fake_response.json.return_value = {
            "results": [
                {
                    "title": "Ollama",
                    "url": "https://ollama.com/",
                    "content": "Cloud models are now available...",
                }
            ]
        }

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "test-key"}),
            mock.patch("httpx.post", return_value=fake_response) as post,
        ):
            web_search.web_search("what is ollama?")

        # Single POST to the web_search endpoint.
        post.assert_called_once()
        call_args = post.call_args
        self.assertEqual(call_args.args[0], "https://ollama.com/api/web_search")
        # Bearer auth header.
        headers = call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        # JSON body with the query.
        body = call_args.kwargs["json"]
        self.assertEqual(body, {"query": "what is ollama?", "max_results": 5})

    def test_web_search_passes_max_results_through(self) -> None:
        from grc_agent.runtime import web_search

        fake_response = mock.MagicMock()
        fake_response.raise_for_status = mock.MagicMock()
        fake_response.json.return_value = {"results": []}

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}),
            mock.patch("httpx.post", return_value=fake_response) as post,
        ):
            web_search.web_search("q", max_results=3)

        self.assertEqual(post.call_args.kwargs["json"], {"query": "q", "max_results": 3})

    def test_web_search_clamps_max_results_to_10(self) -> None:
        """Per the API contract, max_results max is 10. Above that the API
        rejects the request; clamp to 10 before calling."""
        from grc_agent.runtime import web_search

        fake_response = mock.MagicMock()
        fake_response.raise_for_status = mock.MagicMock()
        fake_response.json.return_value = {"results": []}

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}),
            mock.patch("httpx.post", return_value=fake_response) as post,
        ):
            web_search.web_search("q", max_results=999)

        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 10)

    def test_web_fetch_posts_url_to_ollama_endpoint(self) -> None:
        from grc_agent.runtime import web_search

        fake_response = mock.MagicMock()
        fake_response.raise_for_status = mock.MagicMock()
        fake_response.json.return_value = {
            "title": "Ollama",
            "content": "Cloud models are now available in Ollama...",
            "links": ["https://ollama.com/", "https://ollama.com/models"],
        }

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "test-key"}),
            mock.patch("httpx.post", return_value=fake_response) as post,
        ):
            web_search.web_fetch("https://ollama.com")

        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://ollama.com/api/web_fetch")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(post.call_args.kwargs["json"], {"url": "https://ollama.com"})


class WebSearchResultShapeTests(unittest.TestCase):
    """The wrappers return the standard ``ok / results`` payload used
    across the runtime, so downstream formatters (``tool_history_content_as_text``)
    can consume them the same way as catalog/docs results."""

    def _ok(self, payload):
        r = mock.MagicMock()
        r.raise_for_status = mock.MagicMock()
        r.json.return_value = payload
        return r

    def test_web_search_returns_results_array(self) -> None:
        from grc_agent.runtime import web_search

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}),
            mock.patch(
                "httpx.post",
                return_value=self._ok(
                    {
                        "results": [
                            {
                                "title": "A",
                                "url": "https://example.com/a",
                                "content": "snippet A",
                            },
                            {
                                "title": "B",
                                "url": "https://example.com/b",
                                "content": "snippet B",
                            },
                        ]
                    }
                ),
            ),
        ):
            result = web_search.web_search("q")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["title"], "A")
        self.assertEqual(result["results"][1]["url"], "https://example.com/b")

    def test_web_search_empty_results(self) -> None:
        from grc_agent.runtime import web_search

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}),
            mock.patch("httpx.post", return_value=self._ok({"results": []})),
        ):
            result = web_search.web_search("nothing")

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"], [])

    def test_web_fetch_returns_title_content_links(self) -> None:
        from grc_agent.runtime import web_search

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}),
            mock.patch(
                "httpx.post",
                return_value=self._ok(
                    {
                        "title": "Ollama",
                        "content": "Cloud models are now available...",
                        "links": ["https://ollama.com/"],
                    }
                ),
            ),
        ):
            result = web_search.web_fetch("https://ollama.com")

        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Ollama")
        self.assertIn("Cloud models", result["content"])
        self.assertEqual(result["links"], ["https://ollama.com/"])


class WebSearchErrorHandlingTests(unittest.TestCase):
    """Auth / network errors must surface as a typed ``ok=False``
    payload, NOT as an unhandled exception. The runtime policy is
    that wrapper errors are encoded in the result, not raised."""

    def test_missing_api_key_returns_typed_error(self) -> None:
        from grc_agent.runtime import web_search

        with mock.patch.dict(os.environ, {}, clear=True):
            result = web_search.web_search("q")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "missing_api_key")
        self.assertIn("OLLAMA_API_KEY", result["message"])

    def test_http_error_returns_typed_error(self) -> None:
        import httpx
        from grc_agent.runtime import web_search

        with (
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}),
            mock.patch(
                "httpx.post",
                side_effect=httpx.HTTPError("401 Unauthorized"),
            ),
        ):
            result = web_search.web_search("q")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "network_error")
        self.assertIn("401", result["message"])


class WebSearchAgentIntegrationTests(unittest.TestCase):
    """The agent must surface web_search and web_fetch through the
    MVP model-facing tool surface, with the standard handler
    dispatch."""

    def test_agent_registry_includes_web_tools(self) -> None:
        from grc_agent.agent import GrcAgent

        agent = GrcAgent()
        names = set(agent._mvp_tools.keys())
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_agent_handlers_route_to_web_search_module(self) -> None:
        from grc_agent import agent as agent_module
        from grc_agent.agent import GrcAgent
        from grc_agent.runtime import web_search

        agent = GrcAgent()

        # Monkeypatch the module-level web_search call to assert the
        # agent's handler forwards query + max_results correctly.
        with (
            mock.patch.object(
                web_search, "web_search", return_value={"ok": True, "results": []}
            ) as patched,
            mock.patch.dict(os.environ, {"OLLAMA_API_KEY": "k"}),
        ):
            result = agent_module.GrcAgent._web_search(agent, "test query", max_results=7)

        self.assertTrue(result["ok"])
        patched.assert_called_once_with(query="test query", max_results=7)

    def test_model_facing_surfaces_include_web_tools(self) -> None:
        from grc_agent.runtime.model_context import MVP_TOOL_SURFACE

        names = set(MVP_TOOL_SURFACE.model_tool_names)
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_tool_schemas_include_web_tools(self) -> None:
        from grc_agent.runtime.tool_schemas import build_tool_schemas

        names = {s["function"]["name"] for s in build_tool_schemas()}
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)


class WebSearchSchemaContractTests(unittest.TestCase):
    """The model-facing tool schemas must follow the same strict
    schema contract as the other MVP tools (no extra properties,
    declared required fields)."""

    def test_web_search_schema_shape(self) -> None:
        from grc_agent.runtime.tool_schemas import build_tool_schemas

        schemas = {s["function"]["name"]: s for s in build_tool_schemas()}
        ws = schemas["web_search"]
        assert ws["function"]["strict"] is True
        params = ws["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert "query" in params["properties"]
        assert "max_results" in params["properties"]
        assert params["required"] == ["query"]

    def test_web_fetch_schema_shape(self) -> None:
        from grc_agent.runtime.tool_schemas import build_tool_schemas

        schemas = {s["function"]["name"]: s for s in build_tool_schemas()}
        wf = schemas["web_fetch"]
        assert wf["function"]["strict"] is True
        params = wf["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert "url" in params["properties"]
        assert params["required"] == ["url"]


if __name__ == "__main__":
    unittest.main()
