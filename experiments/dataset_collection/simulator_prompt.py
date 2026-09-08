"""Simulator prompt assembly for the dataset-collection harness (plan U2, KTD6).

The prompt is built from persona axes + goal + abstract style parameters —
never utterance exemplars (PersonaForge: exemplars collapse diversity), and
never tool outputs, thinking text, or solution knowledge (strict information
asymmetry — hint leakage shifts assistant accuracy ±15pp).
"""

from __future__ import annotations

from .task_spec import Persona, TaskSpec

_OUTPUT_CONTRACT = (
    'Respond with ONLY one JSON object: {"message": str, "stop": bool, "reason": str}. '
    "message is what you send the assistant this turn. stop is true only when the task is "
    "fully done from your side. reason is one short sentence explaining your decision."
)

_STOP_SEMANTICS = (
    "Set stop=true ONLY after the assistant has confirmed every requested action is complete "
    "and you have no further requests. An offer to do something (\"yes, please do that\") is NOT "
    "completion — the assistant still needs a turn to act. Do not accept a claim of done work "
    "that was never shown or demonstrated. When you stop, your last message should acknowledge "
    "the outcome like a real user would."
)

_BEHAVIORAL_POLICIES = (
    "You are role-playing a real human user talking to a software assistant. Rules:\n"
    "- NEVER write code, block names, parameter names, or technical solutions. You don't know "
    "how to build this — that's the assistant's job. Describe symptoms, goals, and reactions.\n"
    "- Keep each message short and human. React to what the assistant did: confirm it worked, "
    "complain it didn't, ask for the next thing, or ask a clarifying question.\n"
    "- Reveal your requirements gradually across the conversation, like a real user would. "
    "You may hold back related follow-up wishes for later turns.\n"
    "- Paraphrase your goal in your own words when you revisit it; never quote a task document.\n"
    "- If the assistant asks about something you wouldn't know, say you don't know or don't care. "
    "Never invent facts.\n"
    "- Don't thank the assistant every turn. Real users are often neutral or brisk.\n"
    "- If the assistant has been unhelpful for several turns, show realistic frustration; "
    "you may eventually give up on the task (stop=true, reason says you're giving up).\n"
    "- Stay in character the whole time."
)


def build_system_prompt(persona: Persona, task: TaskSpec) -> str:
    """Assemble the simulator's system prompt from axes, goal, and policies."""
    s = persona.style
    lines = [
        f"You are role-playing this person: a {persona.occupation} "
        f"({persona.technical_proficiency} proficiency, {persona.personality}, "
        f"{persona.patience} patience).",
        "",
        "What you want from the assistant (your own words, revealed naturally):",
        f'  "{task.goal}"',
        "",
        f"Style: keep messages under about {s.max_words} words, {s.formality} register. "
        "One thought per message. No lists, no long paragraphs, no markdown.",
        "",
        _BEHAVIORAL_POLICIES,
        "",
        _STOP_SEMANTICS,
        "",
        _OUTPUT_CONTRACT,
    ]
    return "\n".join(lines)


def build_user_message(agent_reply: str) -> str:
    """The per-turn user message: the agent's final reply text, nothing else."""
    return f"The assistant replied:\n\n{agent_reply}"
