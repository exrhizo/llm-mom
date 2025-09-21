import re
import time
from re import Pattern

import libtmux
from libtmux.pane import Pane

from mom.lib.logger import get_logger

log = get_logger(__name__)

class ManagedPane(Pane):
    """
    A ``Pane`` with extra lifecycle helpers.

    Adds
    -----
    alive()          - tmux-level liveness check (pane.refresh()).
    initialized()    - True when *init_regex* has ever appeared in history.
    idle_for()       - Seconds since **any** change in the pane.
    """

    # We inject these three private attrs at construction time via
    # ``ManagedPane.from_existing`` - they are **not** part of libtmux.Pane.
    _init_regex: Pattern[str] | None
    _matches_init_regex: bool | None
    _last_snapshot: str | None
    last_activity: float
    pane_id: str | None = None

    # ------------- construction helpers ------------- #
    @classmethod
    def from_existing(
        cls,
        pane: Pane,
        init_regex: str | Pattern[str] | None = None,
    ) -> "ManagedPane":
        """
        Upgrade an already-created :class:`Pane` instance *in-place* to a
        :class:`ManagedPane`.  Fast and keeps any tmux handles alive.

        >>> raw = window.split()
        >>> managed = ManagedPane.from_existing(raw, init_regex=r"READY")
        """
        pane.__class__ = cls
        pane._init_regex = re.compile(init_regex) if isinstance(init_regex, str) else init_regex # type: ignore[reportAttributeAccessIssue]
        pane._matches_init_regex = None  # type: ignore[reportAttributeAccessIssue]
        pane._last_snapshot = None  # type: ignore[reportAttributeAccessIssue]
        pane.last_activity = time.time()  # type: ignore[reportAttributeAccessIssue]
        return pane  # type: ignore[return-value]

    # -------------------- status -------------------- #
    @property
    def alive(self) -> bool:
        try:
            self.refresh()
            return True
        except Exception as e:
            log.error(f"Error refreshing pane {self.pane_id}: {e}")
            return False

    @property
    def initialized(self) -> bool:
        if self._init_regex is None:  # always ready if no regex supplied
            return True
        try:
            hist = self.capture_pane() or []
            return bool(self._init_regex.search("\n".join(hist)))
        except Exception as e:
            log.error(f"Error checking initialization of {self.pane_id}: {e}")
            return False

    @property
    def idle_for(self) -> float:
        """Time since *any* textual change in the pane (whole buffer)."""
        try:
            snapshot = "\n".join(self.capture_pane() or [])
            if snapshot != getattr(self, "_last_snapshot", None):
                self._last_snapshot = snapshot
                self.last_activity = time.time()
        except Exception as e:
            log.error(f"Error capturing pane {self.pane_id}: {e}")
        return time.time() - self.last_activity

def managed_pane_from_id(pane_id: str, init_regex: str | Pattern[str] | None = None) -> ManagedPane:
    server = libtmux.Server()
    # libtmux supports get_by_id for %, @, $ ids
    obj = server.get_by_id(pane_id)  # e.g., "%7"
    if not isinstance(obj, Pane):
        raise RuntimeError(f"tmux pane not found: {pane_id}")
    mp = ManagedPane.from_existing(obj, init_regex=init_regex)
    mp.pane_id = pane_id  # for your logger field
    return mp
