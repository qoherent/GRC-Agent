"""Thin runtime wrapper for tool-driven `.grc` sessions."""

from typing import Any, Callable

from grc_agent.flowgraph_session import FlowgraphSession

ToolCallable = Callable[..., Any]
HistoryEntry = dict[str, Any]


class GrcAgent:
    """A thin integration layer between a language model and a FlowgraphSession."""

    def __init__(self, session: FlowgraphSession) -> None:
        self.session = session
        self.history: list[HistoryEntry] = []
        self._tools = self._build_tool_registry()

    def get_system_prompt(self) -> str:
        return (
            "You are a GRC (GNU Radio Companion) Agent.\n"
            "Your job is to safely modify .grc files using the provided tools.\n"
            "Rules:\n"
            "1. Do not ask the user for information you can get by calling `summarize`.\n"
            "2. Always call `validate` after making structural changes (connect, disconnect, remove, add) to ensure the graph is sound.\n"
            "3. If validation fails, investigate the error and fix it before saving.\n"
            "4. Only save if the graph is valid."
        )

    def _build_tool_registry(self) -> dict[str, ToolCallable]:
        # A mapping of tool names to functions wrapping the session.
        # In a real model integration, this would also include JSON Schema definitions.
        return {
            "summarize": lambda: self.session.summarize(),
            "validate": lambda: "Valid" if self.session.validate() else "Invalid (see logs or throw)",
            "save": lambda path=None: self.session.save(path),
            "set_param": self.session.set_param,
            "disconnect": self.session.disconnect,
            "connect": self.session.connect,
            "remove_block": self.session.remove_block,
            "add_block": self.session.add_block,
            "add_and_connect_qtgui_time_sink": self.session.add_and_connect_qtgui_time_sink,
            "add_and_connect_char_to_float_to_qtgui_time_sink": self.session.add_and_connect_char_to_float_to_qtgui_time_sink,
            "add_and_connect_analog_random_source_to_qtgui_time_sink": self.session.add_and_connect_analog_random_source_to_qtgui_time_sink,
        }

    def execute_tool(self, tool_name: str, kwargs: dict[str, Any]) -> str:
        """Execute a tool and return its result as a string."""
        if tool_name not in self._tools:
            return f"Error: Unknown tool '{tool_name}'"
        
        func = self._tools[tool_name]
        try:
            result = func(**kwargs)
            if result is None:
                return f"Success: {tool_name} completed."
            return str(result)
        except Exception as error:
            return f"Tool Error ({tool_name}): {error}"

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

                # Update history as if assistant requested a tool
                self.history.append({
                    "role": "assistant",
                    "tool_calls": [{"name": tool_name, "arguments": kwargs}]
                })

                # Execute tool
                result = self.execute_tool(tool_name, kwargs)
                print(f"Tool {tool_name} responded: {result}")

                # Provide response back
                self.history.append({
                    "role": "tool",
                    "name": tool_name,
                    "content": result
                })
