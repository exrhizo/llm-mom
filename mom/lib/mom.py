import subprocess
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue

from libtmux.pane import Pane
from pydantic_ai import Agent

from mom.config import c_env
from mom.lib.llm import AssessOut, NextStep, build_assess_prompt, build_prompt
from mom.lib.tmuxctl import TmuxCtl


@dataclass
class TranscriptEntry:
    role: str
    text: str
    ts: float


@dataclass
class WaitAfterReport:
    bash_wait: str | None


Event = WaitAfterReport


class Watcher(threading.Thread):
    def __init__(self, name: str, pane: Pane, strategy_plan: str, agent: Agent[None, NextStep], assessor: Agent[None, AssessOut]):
        super().__init__(daemon=True)
        self.name = name
        self.pane = pane
        self.strategy_plan = strategy_plan
        self.agent = agent
        self.assessor = assessor

        self.paused: bool = False
        self._stop_event = threading.Event()
        self.transcript: list[TranscriptEntry] = [TranscriptEntry("plan", strategy_plan, time.time())]
        self.latest_status: str = ""
        self.latest_injection: NextStep | None = None

        self.events: Queue[Event] = Queue()
        self._last_snapshot: str = ""
        self._last_change_ts: float = time.time()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                ev = self.events.get(timeout=c_env.POLL_SECS)
            except Empty:
                continue

            if isinstance(ev, WaitAfterReport):
                wait_output = self._do_wait(ev.bash_wait)
                self.transcript.append(TranscriptEntry("wait_output", wait_output, time.time()))

                self._spin_until_idle()

                decision = self._assess(wait_output)
                if decision is None:
                    self.paused = True
                    self.transcript.append(TranscriptEntry("decision", "stop", time.time()))
                else:
                    self.pane.send_keys(decision, enter=c_env.INJECT_PRESS_ENTER)
                    self.transcript.append(TranscriptEntry("injection", decision, time.time()))
                    self.transcript.append(TranscriptEntry("decision", "continue", time.time()))

    def stop(self) -> None:
        self._stop_event.set()

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

    def _pane_text(self) -> str:
        return TmuxCtl.tail(self.pane, c_env.TAIL_LINES)

    def _update_idle(self) -> None:
        snap = self._pane_text()
        if snap != self._last_snapshot:
            self._last_snapshot = snap
            self._last_change_ts = time.time()

    @property
    def idle_for(self) -> float:
        return time.time() - self._last_change_ts

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
            self._update_idle()
            if self.idle_for >= c_env.IDLE_THRESHOLD:
                break
            time.sleep(c_env.IDLE_SPIN_POLL_SECS)

        self.transcript.append(
            TranscriptEntry("idle_spin", f"idle_for={self.idle_for:.2f}", time.time())
        )

    def _assess(self, wait_output: str) -> None | str:
        transcript_tail = self._format_transcript_tail()
        pane_tail = self._pane_text()
        prompt = build_assess_prompt(self.strategy_plan, transcript_tail, wait_output, pane_tail)
        result = self.assessor.run_sync(prompt)
        assessment = result.output

        if assessment.action == "stop":
            return None
        else:
            return assessment.injection_prompt

    def _format_transcript_tail(self) -> str:
        entries = self.transcript[-30:]  # Last 30 entries
        entries.reverse()  # Most recent first

        formatted = []
        for entry in entries:
            timestamp = time.strftime("-%H:%M:%S", time.localtime(entry.ts))
            text = entry.text[:200]  # Trim to ~200 chars
            formatted.append(f"[{timestamp} {entry.role}] {text}")

        return "\n".join(formatted)

    def _synthesize_next_step(self) -> NextStep:
        tail = TmuxCtl.tail(self.pane, c_env.TAIL_LINES)
        prompt = build_prompt(self.strategy_plan, self.latest_status, tail)
        result = self.agent.run_sync(prompt)
        return result.output



class Mom:
    def __init__(self, tmux: TmuxCtl, agent: Agent[None, NextStep], assessor: Agent[None, AssessOut]):
        self.tmux = tmux
        self.agent = agent
        self.assessor = assessor
        self.watchers: dict[str, Watcher] = {}
        self.last_by_client: dict[str, str] = {}  # client_id -> tmux_window

    def set_active_for_client(self, client_id: str | None, tmux_window: str) -> None:
        if client_id:
            self.last_by_client[client_id] = tmux_window

    def _resolve_window(self, client_id: str | None, tmux_window: str | None) -> str:
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
        w = Watcher(tmux_window, pane, strategy_plan, self.agent, self.assessor)
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

    def look_ma(self, client_id: str | None, status_report: str, tmux_window: str | None, bash_wait: str | None = None) -> str:
        key = self._resolve_window(client_id, tmux_window)
        watcher = self.watchers[key]
        watcher.add_status(status_report)
        watcher.events.put(WaitAfterReport(bash_wait))
        return "recorded+waiting"

    @property
    def attach_cmd(self) -> str:
        return self.tmux.attach_cmd
