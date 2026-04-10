"""Thin runtime wrapper for tool-driven `.grc` sessions."""

import json
from typing import Any, Callable

from grc_agent.flowgraph_session import FlowgraphSession

ToolResult = dict[str, Any]
ToolCallable = Callable[..., ToolResult]
HistoryEntry = dict[str, Any]


class GrcAgent:
    """A thin integration layer between a language model and a FlowgraphSession."""

    def __init__(self, session: FlowgraphSession) -> None:
        self.session = session
        self.history: list[HistoryEntry] = []
        self._mutation_revision = 0
        self._last_validated_revision: int | None = 0 if not session.is_dirty else None
        self._last_validation_ok: bool | None = None
        self._tools = self._build_tool_registry()

    def get_system_prompt(self) -> str:
        return (
            "You are a GRC (GNU Radio Companion) Agent.\n"
            "Your job is to inspect and safely modify .grc files using only the provided tools.\n"
            "Rules:\n"
            "1. Start by calling `summarize_graph` when you need graph context.\n"
            "2. Use `set_variable` only for GNU Radio `variable` blocks.\n"
            "3. Call `validate_graph` before asking to save a dirty graph.\n"
            "4. Only use `save_graph` after the latest dirty state has validated successfully.\n"
            "5. Most tool results are JSON objects.\n"
            "6. After `summarize_graph`, the latest tool message content is the final summary text. "
            "Copy that tool message content verbatim as your final answer.\n"
            "7. Never leave the final answer empty after `summarize_graph`.\n"
            "8. Do not add markdown, commentary, introductions, conclusions, or follow-up questions "
            "after `summarize_graph`.\n"
            "9. After any other successful tool flow, return one short factual sentence and do not "
            "leave the final answer empty.\n"
            "10. Keep final answers short and factual."
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the fixed tool schemas exposed to a chat-completions client."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "summarize_graph",
                    "description": "Return a short summary of the loaded GNU Radio graph.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_variable",
                    "description": (
                        "Update the value parameter on a GNU Radio variable block. "
                        "Use this only for blocks whose type is variable."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_name": {
                                "type": "string",
                                "description": "Variable block instance name.",
                            },
                            "value": {
                                "type": ["string", "number", "boolean"],
                                "description": "New variable value or expression.",
                            },
                        },
                        "required": ["instance_name", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "validate_graph",
                    "description": "Run grcc validation on the current in-memory graph.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_graph",
                    "description": (
                        "Save the current graph to disk. This is allowed only after the latest "
                        "dirty state has validated successfully."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional destination path for the saved .grc file.",
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def get_model_messages(self) -> list[HistoryEntry]:
        """Render the current runtime history into chat-completions messages."""
        messages: list[HistoryEntry] = [
            {
                "role": "system",
                "content": self.get_system_prompt(),
            }
        ]

        for index, turn in enumerate(self.history):
            role = turn.get("role")

            if role == "tool":
                tool_name = turn.get("name")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(turn.get("tool_call_id") or f"tool_call_{index}"),
                        "name": tool_name,
                        "content": self._history_content_as_text(
                            turn.get("content"),
                            tool_name=tool_name,
                        ),
                    }
                )
                continue

            if role not in {"user", "assistant"}:
                continue

            message: HistoryEntry = {
                "role": role,
                "content": turn.get("content"),
            }
            if role == "assistant" and "tool_calls" in turn:
                message["tool_calls"] = turn["tool_calls"]
            messages.append(message)

        return messages

    def _build_tool_registry(self) -> dict[str, ToolCallable]:
        return {
            "summarize_graph": self._summarize_graph,
            "set_variable": self._set_variable,
            "validate_graph": self._validate_graph,
            "save_graph": self._save_graph,
        }

    def execute_tool(self, tool_name: str, kwargs: dict[str, Any]) -> ToolResult:
        """Execute one runtime tool and return a structured result."""
        if tool_name not in self._tools:
            return self._tool_result(
                tool_name=tool_name,
                ok=False,
                message=f"Unknown tool: {tool_name}",
                error_type="UnknownTool",
            )

        func = self._tools[tool_name]
        try:
            return func(**kwargs)
        except Exception as error:
            return self._tool_result(
                tool_name=tool_name,
                ok=False,
                message=str(error),
                error_type=type(error).__name__,
            )

    def _tool_result(self, tool_name: str, ok: bool, message: str, **extra: Any) -> ToolResult:
        """Build the common structured result payload returned by every tool."""
        result: ToolResult = {
            "tool": tool_name,
            "ok": ok,
            "message": message,
        }
        result.update(extra)
        return result

    def _require_loaded_flowgraph(self) -> Any:
        """Return the loaded flowgraph or raise when the session is empty."""
        if self.session.flowgraph is None:
            raise ValueError("No flowgraph loaded.")
        return self.session.flowgraph

    def _mark_mutation(self) -> None:
        """Advance the dirty revision and invalidate any prior validation result."""
        self._mutation_revision += 1
        self._last_validated_revision = None
        self._last_validation_ok = None

    def _history_content_as_text(self, content: Any, *, tool_name: str | None = None) -> str:
        """Normalize stored history content into the string form chat APIs expect."""
        if (
            tool_name == "summarize_graph"
            and isinstance(content, dict)
            and isinstance(content.get("summary"), str)
        ):
            return content["summary"]
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        if isinstance(content, (dict, list)):
            return json.dumps(content, sort_keys=True)
        return str(content)

    def _summarize_graph(self) -> ToolResult:
        return self._tool_result(
            tool_name="summarize_graph",
            ok=True,
            message="Graph summary generated.",
            summary=self.session.summarize(),
            dirty=self.session.is_dirty,
        )

    def _set_variable(self, instance_name: str, value: Any) -> ToolResult:
        flowgraph = self._require_loaded_flowgraph()
        matches = [
            block for block in flowgraph.blocks if block.instance_name == instance_name
        ]
        if not matches:
            raise ValueError(f"Variable block not found: {instance_name}")
        if len(matches) != 1:
            raise ValueError(f"Variable block name is not unique: {instance_name}")

        block = matches[0]
        if block.block_type != "variable":
            raise ValueError(f"Unsupported variable target: {instance_name}")

        self.session.set_param(instance_name, "value", value)
        self._mark_mutation()
        return self._tool_result(
            tool_name="set_variable",
            ok=True,
            message=f"Updated variable '{instance_name}'.",
            instance_name=instance_name,
            value=value,
            dirty=self.session.is_dirty,
        )

    def _validate_graph(self) -> ToolResult:
        is_valid = self.session.validate()
        self._last_validation_ok = is_valid
        self._last_validated_revision = self._mutation_revision if is_valid else None
        return self._tool_result(
            tool_name="validate_graph",
            ok=True,
            message="Graph is valid." if is_valid else "Graph is invalid.",
            valid=is_valid,
            dirty=self.session.is_dirty,
            stdout=self.session.last_validation_stdout,
            stderr=self.session.last_validation_stderr,
            returncode=self.session.last_validation_returncode,
        )

    def _save_graph(self, path: str | None = None) -> ToolResult:
        if self.session.is_dirty and (
            not self._last_validation_ok
            or self._last_validated_revision != self._mutation_revision
        ):
            return self._tool_result(
                tool_name="save_graph",
                ok=False,
                message="Refusing to save a dirty graph before successful validation.",
                requires_validation=True,
                dirty=True,
            )

        self.session.save(path)
        saved_path = str(self.session.path) if self.session.path is not None else None
        return self._tool_result(
            tool_name="save_graph",
            ok=True,
            message="Graph saved.",
            path=saved_path,
            dirty=self.session.is_dirty,
        )

    def run_step_fake(self, user_msg: str, fake_assistant_actions: list[HistoryEntry]) -> None:
        """
        A fake loop step to test the plumbing.
        fake_assistant_actions is a list of dicts.
        If it has 'tool', it's a tool call. If it has 'text', it's a message.
        """
        self.history.append({"role": "user", "content": user_msg})

        for action in fake_assistant_actions:
            if "text" in action:
                self.history.append({"role": "assistant", "content": action["text"]})
                print(f"Assistant: {action['text']}")

            if "tool" in action:
                tool_name = action["tool"]
                kwargs = action.get("kwargs", {})
                print(f"Assistant called {tool_name} with {kwargs}")

                self.history.append(
                    {
                        "role": "assistant",
                        "tool_calls": [{"name": tool_name, "arguments": kwargs}],
                    }
                )

                result = self.execute_tool(tool_name, kwargs)
                print(f"Tool {tool_name} responded: {result}")

                self.history.append(
                    {
                        "role": "tool",
                        "name": tool_name,
                        "content": result,
                    }
                )
