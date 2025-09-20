from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent


class NextStep(BaseModel):
    injection_prompt: str
    achieved: bool


class AssessOut(BaseModel):
    action: Literal["stop", "continue"]
    injection_prompt: str | None


def make_agent(model_name: str) -> Agent[None, NextStep]:
    instructions = (
        "You are 'mom', the strict conductor. "
        "Given a strategy plan, the latest status report, and a tmux pane tail, "
        "produce a single short directive for the coder/agent to execute next. "
        "If the goal is already achieved, set achieved=true and return an empty directive. "
        "Rules:\n"
        "- Keep the injection_prompt imperative, <= 2 sentences, concrete, no meta-talk.\n"
        "- Never restate the plan unless it changes an action. No speculation."
    )
    return Agent(model_name, output_type=NextStep, instructions=instructions)


def make_assessor(model_name: str) -> Agent[None, AssessOut]:
    instructions = (
        "You are \"mom\", the conductor. Decide if work is done. If not done, provide one "
        "short imperative command to inject into an interactive CLI. Never explain.\n\n"
        "Rules:\n"
        "- Use only information present in transcript, wait_output, and pane_tail.\n"
        "- If the goal appears complete, action=\"stop\" and injection_prompt=null.\n"
        "- If more work is needed, action=\"continue\" and injection_prompt=\"...\".\n"
        "- Injection must be <= 160 chars. No commentary."
    )
    return Agent(model_name, output_type=AssessOut, instructions=instructions)


def build_prompt(strategy_plan: str, status_report: str, pane_tail: str) -> str:
    return (
        "Context:\n"
        f"Strategy Plan:\n{strategy_plan}\n\n"
        f"Latest Status Report:\n{status_report}\n\n"
        f"Pane Tail (most recent lines):\n{pane_tail}\n\n"
        "Task: Output the next concrete step as `injection_prompt`. "
        "If the plan appears complete, set `achieved=true`."
    )


def build_assess_prompt(strategy_plan: str, transcript_tail: str, wait_output: str, pane_tail: str) -> str:
    return (
        f"Strategy Plan:\n{strategy_plan}\n\n"
        f"Transcript (most-recent-first, terse):\n{transcript_tail}\n\n"
        f"Wait Output (stdout/stderr, truncated):\n{wait_output}\n\n"
        f"Pane Tail (recent lines, truncated):\n{pane_tail}\n\n"
        "Task:\nReturn JSON with fields: action, injection_prompt."
    )
