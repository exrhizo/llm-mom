import time

import libtmux
from libtmux.pane import Pane
from libtmux.window import Window


class TmuxCtl:
    def __init__(self, session_name: str, window_name: str) -> None:
        self.server = libtmux.Server()
        self.session_name = session_name
        self.window_name = window_name
        self.window: Window = self._ensure_window()

    def _ensure_window(self) -> Window:
        session = self.server.find_where({"session_name": self.session_name})
        if not session:
            session = self.server.new_session(
                session_name=self.session_name,
                window_name=self.window_name,
                attach=False,
                kill_session=False,
            )
        win = session.attached_window
        if win.name != self.window_name:
            win.rename_window(self.window_name)
        if not win.panes:
            win.split_window(attach=False)
        win.select_layout("tiled")
        return win

    def ensure_pane(self, label: str) -> Pane:
        # reuse last pane if label already printed in it, else split
        for p in self.window.panes:
            try:
                lines = p.capture_pane() or []
                if lines and any(label in ln for ln in lines[-5:]):
                    return p
            except Exception:
                continue
        base = self.window.panes[-1]
        pane = base.split(attach=False)
        time.sleep(0.1)
        pane.send_keys(f"clear; printf '[{label}]\\n'", enter=True)
        self.window.select_layout("tiled")
        return pane

    @staticmethod
    def tail(pane: Pane, n: int) -> str:
        try:
            lines = pane.capture_pane() or []
            return "\n".join(lines[-n:])
        except Exception:
            return ""

    @property
    def attach_cmd(self) -> str:
        return f"tmux attach -t {self.session_name}"
