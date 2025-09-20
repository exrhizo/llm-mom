import threading
import time
from dataclasses import dataclass
from typing import Optional

from libtmux.pane import Pane
from pydantic_ai import Agent

from mom.config import c_env
from mom.lib.llm import NextStep, build_prompt
from mom.lib.tmuxctl import TmuxCtl


@dataclass
class TranscriptEntry:
    role: str
    text: str
    ts: float


class Watcher(threading.Thread):
    def __init__(self, name: str, pane: Pane, strategy_plan: str, agent: Agent[None, NextStep]):
        super().__init__(daemon=True)
        self.name = name
        self.pane = pane
        self.strategy_plan = strategy_plan
        self.agent = agent

        self.paused: bool = False
        self._stop = threading.Event()
        self.transcript: list[TranscriptEntry] = [TranscriptEntry("plan", strategy_plan, time.time())]
        self.latest_status: str = ""
        self.latest_injection: Optional[NextStep] = None

    def run(self) -> None:
        while not self._stop.is_set():
            time.sleep(c_env.POLL_SECS)
            # Light-touch background loop; decisioning happens when paused() or via explicit tool calls.

    def stop(self) -> None:
        self._stop.set()

    def update_plan(self, strategy_plan: str) -> None:
        self.strategy_plan = strategy_plan
        self.transcript.append(TranscriptEntry("plan", strategy_plan, time.time()))

    def add_status(self, status_report: str) -> None:
        self.latest_status = status_report
        self.transcript.append(TranscriptEntry("status", status_report, time.time()))
        # Trim
        if len(self.transcript) > c_env.MAX_TRANSCRIPT:
            self.transcript = self.transcript[-c_env.MAX_TRANSCRIPT :]

    def pause(self) -> NextStep:
        self.paused = True
        next_step = self._synthesize_next_step()
        self.latest_injection = next_step
        self.transcript.append(
            TranscriptEntry(
                "injection",
                f"achieved={next_step.achieved}; {next_step.injection_prompt}",
                time.time(),
            )
        )
        if next_step.achieved:
            # stay paused
            pass
        return next_step

    def clear(self) -> None:
        self.stop()

    def _synthesize_next_step(self) -> NextStep:
        tail = TmuxCtl.tail(self.pane, c_env.TAIL_LINES)
        prompt = build_prompt(self.strategy_plan, self.latest_status, tail)
        result = self.agent.run_sync(prompt)
        return result.output


class Mom:
    def __init__(self, tmux: TmuxCtl, agent: Agent[None, NextStep]):
        self.tmux = tmux
        self.agent = agent
        self.watchers: dict[str, Watcher] = {}
        self.last_by_client: dict[str, str] = {}  # client_id -> tmux_window

    def set_active_for_client(self, client_id: str | None, tmux_window: str) -> None:
        if client_id:
            self.last_by_client[client_id] = tmux_window

    def _resolve_window(self, client_id: str | None, tmux_window: Optional[str]) -> str:
        if tmux_window:
            return tmux_window
        if client_id and client_id in self.last_by_client:
            return self.last_by_client[client_id]
        if self.watchers:
            return next(iter(self.watchers.keys()))
        raise KeyError("No watcher exists yet.")

    def watch_me(self, tmux_window: str, strategy_plan: str) -> str:
        if tmux_window in self.watchers:
            self.watchers[tmux_window].update_plan(strategy_plan)
            return "updated"
        pane = self.tmux.ensure_pane(tmux_window)
        w = Watcher(tmux_window, pane, strategy_plan, self.agent)
        self.watchers[tmux_window] = w
        w.start()
        return "watching"

    def pause(self, tmux_window: str) -> NextStep:
        w = self.watchers[tmux_window]
        return w.pause()

    def clear(self, tmux_window: str) -> str:
        w = self.watchers.pop(tmux_window, None)
        if not w:
            return "noop"
        w.clear()
        return "cleared"

    def look_ma(self, client_id: str | None, status_report: str, tmux_window: Optional[str]) -> str:
        key = self._resolve_window(client_id, tmux_window)
        self.watchers[key].add_status(status_report)
        return "recorded"

    @property
    def attach_cmd(self) -> str:
        return self.tmux.attach_cmd
