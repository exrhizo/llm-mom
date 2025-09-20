import subprocess
from typing import Literal
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Queue

from pydantic_ai import Agent

from mom.config import c_env
from mom.lib.llm import MetaDecision, build_prompt
from mom.lib.tmux_pane import managed_pane_from_id

Role = Literal["meta_goal", "wait_output", "decision", "sub_agent_status"]

@dataclass
class TranscriptEntry:
    role: Role
    text: str
    ts: float = field(default_factory=time.time)


@dataclass
class WaitAfterReport:
    ...


Event = WaitAfterReport


class Watcher(threading.Thread):
    def __init__(self, pane_id: str, meta_goal: str, agent: Agent[None, MetaDecision], wait_cmd: str | None = None):
        super().__init__(daemon=True)
        self.pane = managed_pane_from_id(pane_id)
        self.pane_id = pane_id
        self.meta_goal = meta_goal
        self.agent = agent
        
        self.meta_goal: str = meta_goal
        self.wait_cmd = wait_cmd

        self.transcript: list[TranscriptEntry] = [TranscriptEntry("meta_goal", meta_goal)]

        self.events: Queue[Event] = Queue()
        self._stop_event = threading.Event()


    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                ev = self.events.get(timeout=c_env.POLL_SECS)
            except Empty:
                continue

            if isinstance(ev, WaitAfterReport): # type: ignore[reportUnnecessaryIsinstance]
                wait_output = self._do_wait(self.wait_cmd)
                self.transcript.append(TranscriptEntry("wait_output", wait_output))

                if self._stop_event.is_set():
                    break

                self._spin_until_idle()

                if self._stop_event.is_set():
                    break

                decision = self._next_step(wait_output)
                if decision.action == "continue" and not decision.command:
                    self.transcript.append(TranscriptEntry("decision", "Missing command to continue"))
                if decision.action == "stop":
                    self.transcript.append(TranscriptEntry("decision", f"stop '{decision.command}'"))
                else:
                    self.pane.send_keys(decision.command, enter=c_env.INJECT_PRESS_ENTER)
                    self.transcript.append(TranscriptEntry("decision", f"continue: {decision.command}"))

    def stop(self) -> None:
        self._stop_event.set()
    
    def update_plan(self, meta_goal: str) -> None:
        self.meta_goal = meta_goal
        self.transcript.append(TranscriptEntry("meta_goal", meta_goal))

    def add_status(self, status_report: str) -> None:
        self.latest_status = status_report
        self.transcript.append(TranscriptEntry("sub_agent_status", status_report))

    def _next_step(self, wait_output: str) -> MetaDecision:
        result = self.agent.run_sync(build_prompt(self.meta_goal, self.latest_status, wait_output))
        return result.output

    def _do_wait(self, bash_wait: str | None) -> str:
        if bash_wait:
            result = subprocess.run(
                ["bash", "-lc", bash_wait],
                capture_output=True,
                text=True,
                check=False
            )
            return result.stdout + result.stderr
        else:
            time.sleep(c_env.DEFAULT_WAIT_SEC)
            return f"[sleep] {c_env.DEFAULT_WAIT_SEC:.2f}s"

    def _spin_until_idle(self) -> None:
        while True:
            if self.pane.idle_for >= c_env.IDLE_THRESHOLD:
                break
            time.sleep(c_env.IDLE_SPIN_POLL_SECS)


class Mom:
    def __init__(self, agent: Agent[None, MetaDecision]):
        self.agent = agent
        self.watchers: dict[str, Watcher] = {}

    def attach(self, client_id: str, pane_id: str, meta_goal: str, wait_cmd: str | None = None) -> str:
        if client_id in self.watchers:
            self.watchers[client_id].update_plan(meta_goal)
            return "updated"

        w = Watcher(pane_id, meta_goal, self.agent, wait_cmd)
        self.watchers[client_id] = w
        w.start()
        return "attached"

    def clear(self, client_id: str) -> str:
        w = self.watchers.pop(client_id, None)
        if not w:
            return "noop"
        w.stop()
        return "cleared"

    def look_ma(self, client_id: str, status_report: str) -> str:
        watcher = self.watchers[client_id]
        watcher.add_status(status_report)
        watcher.events.put(WaitAfterReport())
        return "validated"
