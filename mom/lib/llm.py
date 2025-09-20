from pydantic import BaseModel
from pydantic_ai import Agent


class NextStep(BaseModel):
    injection_prompt: str
    achieved: bool


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


def build_prompt(strategy_plan: str, status_report: str, pane_tail: str) -> str:
    return (
        "Context:\n"
        f"Strategy Plan:\n{strategy_plan}\n\n"
        f"Latest Status Report:\n{status_report}\n\n"
        f"Pane Tail (most recent lines):\n{pane_tail}\n\n"
        "Task: Output the next concrete step as `injection_prompt`. "
        "If the plan appears complete, set `achieved=true`."
    )
