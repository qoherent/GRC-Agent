"""Deterministic tests for DocsAnswerAdvisor synthesis contract."""

from __future__ import annotations

import unittest

from grc_agent.runtime.docs_answer_advisor import (
    DocsAnswerAdvisorError,
    DocsAnswerSnippet,
    run_docs_answer_advisor,
)


class _FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def create_chat_completion(self, **_: object) -> dict[str, object]:
        return self._payload


class DocsAnswerAdvisorTests(unittest.TestCase):
    def _snippets(self) -> list[DocsAnswerSnippet]:
        return [
            DocsAnswerSnippet(
                title="Stream Tags",
                source="docs/wiki_gnuradio_org/Stream_Tags.md",
                excerpt="Stream tags annotate stream items with metadata.",
            )
        ]

    def test_success_payload_is_validated(self) -> None:
        client = _FakeClient(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"Stream tags carry metadata.","source_indexes":[0],'
                                '"insufficient_evidence":false}'
                            )
                        }
                    }
                ]
            }
        )
        result = run_docs_answer_advisor(
            client=client,
            model="test",
            question="What are stream tags?",
            answer_type="definition",
            snippets=self._snippets(),
        )
        self.assertEqual(result["answer"], "Stream tags carry metadata.")
        self.assertFalse(result["insufficient_evidence"])
        self.assertEqual(result["source_indexes"], [0])

    def test_invalid_keys_are_rejected(self) -> None:
        client = _FakeClient(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"x","source_indexes":[],"insufficient_evidence":true,"transaction":{"op_type":"update_params"}}'
                            )
                        }
                    }
                ]
            }
        )
        with self.assertRaises(DocsAnswerAdvisorError):
            run_docs_answer_advisor(
                client=client,
                model="test",
                question="What are stream tags?",
                answer_type="definition",
                snippets=self._snippets(),
            )

    def test_non_json_response_is_rejected(self) -> None:
        client = _FakeClient({"choices": [{"message": {"content": "not-json"}}]})
        with self.assertRaises(DocsAnswerAdvisorError):
            run_docs_answer_advisor(
                client=client,
                model="test",
                question="What are stream tags?",
                answer_type="definition",
                snippets=self._snippets(),
            )

    def test_invalid_source_indexes_are_rejected(self) -> None:
        client = _FakeClient(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"x","source_indexes":["a"],"insufficient_evidence":false}'
                            )
                        }
                    }
                ]
            }
        )
        with self.assertRaises(DocsAnswerAdvisorError):
            run_docs_answer_advisor(
                client=client,
                model="test",
                question="What are stream tags?",
                answer_type="definition",
                snippets=self._snippets(),
            )


if __name__ == "__main__":
    unittest.main()
