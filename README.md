# LLM Mom
An MCP “accountability agent” for your tmux pane

**Take back long running agentic workflows**
Mom watches a tmux pane, reads feedback from the world, and—only when your pane is idle—injects a single imperative command. If the job is done or goes weird, it stops.


## Quickstart

* Python 3.12+
* tmux
* An LLM API key in the environment (OpenAI by default)

Install your project as usual, then:

```bash
uv sync
uv run mom up
# started pid 12345 -> logs/mom.log
# claude mcp add mom --url http://127.0.0.1:6541/mcp
```

---
---
---

### Architecture (tiny and opinionated)

```
MCP /mcp (Streamable HTTP)
        │      initialize → initialized → tools/call
        ▼
  mom.lib.mcp_server  ── keeps a process‑global Mom
        │
        ▼
        Mom ── per-session Watcher thread
        │           │
        │           ├─ wait_cmd → subprocess (or sleep)
        │           ├─ pane idle spin (libtmux snapshot)
        │           └─ agent.run_sync(prompt) → MetaDecision
        │
        ▼
   tmux pane (ManagedPane) ⇄ your long-running task
```

### Configuration

- `cp .env.example .env` and update: 

```bash
# required
OPENAI_API_KEY=sk-...

# optional
MODEL=openai:gpt-4.1-nano
MOM_PORT=6541
MOM_LOG_LEVEL=INFO
```

### CLI

```bash
# foreground server
mom serve http

# daemonize (pidfile + logs)
mom start http [--replace]
mom stop
mom status
mom logs [-f] [-n 200]

# convenience: start if not running, then print MCP add line
mom up
```


### Health check:

```bash
curl -s http://127.0.0.1:6541/healthz
# {"ok": true}
```