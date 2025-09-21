## 1) Code review (what’s good, what bites, what to tweak)

**Solid bits**

* Clear separation of roles: `mcp_server` (I/O boundary), `Mom` (state & orchestration), `Watcher` (thread + tmux + LLM loop), `llm.py` (prompt + typed output), `tmux_pane` (pane lifecycle helpers).
* Good use of a typed structured output via `MetaDecision` with `Literal` gating.
* Prompt sanitation (`escape`) and explicit XML tag boundaries reduce prompt‑injection surface.

**Bugs / sharp edges (actionable)**

1. **Logger imports the wrong module and references missing fields**

   * `mom/lib/logger.py` imports `commune.config` and assumes `c_env.LOG_FILE`; neither exist here.
   * Also, `_build_handler` expects a `Path`, but `LOG_FILE` (if it existed) would likely be a `str`.
   * **Fix (concise patch):**

     ```python
     # mom/lib/logger.py
     from pathlib import Path
     from mom.config import c_env  # ✅ correct package

     ...

     def get_logger(name: str, *, fmt: str = _DEFAULT_FMT, level: int | str | None = None) -> logging.Logger:
         path = Path(getattr(c_env, "LOG_FILE", "logs/mom.log"))  # ✅ default & cast
         lvl = level or getattr(c_env, "LOG_LEVEL", "INFO")
         ...
     ```
   * Update the docstring to “`logs/mom.log`”, not “commune.log”.

2. **Multi‑session logic will collide**

   * In `mcp_server.py` you key everything by `ctx.client_id`. Multiple Claude Code chats from the same client share a client ID; you’ll stomp each other. Prefer `ctx.session_id` (unique per session) or a composite `(session_id, pane_id)`. See “2)” below for a crisp fix & why. ([FastMCP][1])

3. **Transcript isn’t actually used; only the latest status is fed to the LLM**

   * `_next_step()` calls:

     ```python
     self.agent.run_sync(build_prompt(self.meta_goal, self.latest_status, wait_output))
     ```

     …but you maintain `self.transcript: list[TranscriptEntry]` and never render it.
   * **Fix (minimal):**

     ```python
     def _render_transcript(self) -> str:
         rows = [f"[{time.strftime('%H:%M:%S', time.localtime(e.ts))}] {e.role}: {e.text}" for e in self.transcript]
         text = "\n".join(rows)
         if len(text) > c_env.MAX_TRANSCRIPT:
             text = text[-c_env.MAX_TRANSCRIPT:]
         return text

     def _next_step(self, wait_output: str) -> MetaDecision:
         trn = self._render_transcript()
         result = self.agent.run_sync(build_prompt(self.meta_goal, trn, wait_output))
         return result.output
     ```

AND, update `build_prompt` to shrink the transcript so that it fits within MODEL_CTX_SIZE -- use the len/3 - ballpark_other
(note that we are using a 1 million token model, so there will be plenty of room)

4. **Race / attribute existence on first run**

   * `self.latest_status` is first set in `add_status()`, but dereferenced in `_next_step()`. If an event were enqueued prematurely, you’d `AttributeError`.
   * **Fix:** initialize in `__init__`: `self.latest_status: str = ""`.

5. **Empty command still gets sent**

   * In `Watcher.run()`, when `action=="continue"` but `command==""`, you log a “Missing command” *and still send* an empty command (pressing Enter). Add a `continue`.
   * **Fix (minimal guard):**

     ```python
     if decision.action == "continue":
         if not decision.command:
             self.transcript.append(TranscriptEntry("decision", "Missing command to continue"))
             continue
         self.pane.send_keys(decision.command, enter=c_env.INJECT_PRESS_ENTER)
         self.transcript.append(TranscriptEntry("decision", f"continue: {decision.command}"))
     elif decision.action == "stop":
         self.transcript.append(TranscriptEntry("decision", f"stop '{decision.command}'"))
         self.stop()
     ```

8. **`managed_pane_from_id` error quality**

   * If the pane ID is wrong, `get_by_id` may return `None`. Your `assert isinstance(obj, Pane)` will raise a bare `AssertionError`. Prefer a clear error:

     ```python
     if not isinstance(obj, Pane):
         raise RuntimeError(f"tmux pane not found: {pane_id}")
     ```

10. **Minor duplication**

* In `Watcher.__init__`, `self.meta_goal` is set twice.
