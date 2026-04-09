"""Thin runtime wrapper for tool-driven `.grc` sessions."""

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
            "4. Only use `save_graph` after the latest dirty state has validated successfully."
        )

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
        result: ToolResult = {
            "tool": tool_name,
            "ok": ok,
            "message": message,
        }
        result.update(extra)
        return result

    def _require_loaded_flowgraph(self) -> Any:
        if self.session.flowgraph is None:
            raise ValueError("No flowgraph loaded.")
        return self.session.flowgraph

    def _mark_mutation(self) -> None:
        self._mutation_revision += 1
        self._last_validated_revision = None
        self._last_validation_ok = None

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
