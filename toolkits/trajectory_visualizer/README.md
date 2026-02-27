# Trajectory Visualizer

Gradio-based UI to visualize agent execution trajectories, inspect individual steps, and compute key performance metrics.

## How to Run

```bash
# From the project root
python -m toolkits.trajectory_visualizer [--port 7860] [--share] [--trajectory-dir PATH]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--port` | `7860` | Server port |
| `--share` | off | Create a public Gradio link |
| `--trajectory-dir` | project root | Base directory for trajectory files |

Trajectory files are discovered under `<trajectory-dir>/generated_patches/trajectory/**/*.json`.

## Module Structure

```
trajectory_visualizer/
  __init__.py      # Package marker (empty)
  __main__.py      # CLI entry point
  data.py          # Data loading, parsing, aggregate metrics, hotspots
  analytics.py     # Per-step analytics, phase detection, behavioral insights
  charts.py        # Plotly chart builders (11 chart types)
  rendering.py     # HTML/code rendering, card styles, workflow HTML, step detail
  app.py           # Gradio UI (build_ui, APP_CSS, callbacks)
  README.md        # This file
```

## Tabs & Features

### Overview

- **KPI strip** — Steps, wall-clock, tokens, tool success rate, cache ratio, non-cache ratio, cost.
- **Session & Environment** — Model, provider, agent, timestamps, session ID, branch, commit, platform.
- **Performance & Tokens** — Duration stats (avg/median/P95/max), full token breakdown (input/output/reasoning/cache read/cache write), non-cache tokens, cost, tokens per tool call, tool call frequency table, agent breakdown, model breakdown.
- **Behavioral Diagnostics** — Multi-tool steps, no-tool steps, median/P95 step tokens, cache-dominant steps, tool execution time, tool-wait share, tool duration stats.
- **Message Hotspots** — Top latency steps, top token-load steps, most expensive steps, lowest cache ratio steps.
- **Per-Message Deep Dive** (accordion) — Full per-message diagnostics table with columns: step, role, agent, finish, duration, tokens, tok/s, cache %, non-cache, out/in ratio, tool calls, tool wait %, cost, parts.

### Charts (Overview tab)

| Chart | Description |
|-------|-------------|
| Token Usage | Stacked bar with non-overlapping segments: fresh input, cache read, output, reasoning. Per-step or cumulative toggle. |
| Step Duration | Bar chart with average line, color-coded by step type. |
| Context Growth | Cumulative input tokens, fresh input, and cache read lines — shows context window pressure. |
| Cost per Step | Dual-axis: per-step cost bar + cumulative cost line. |
| Per-Step Efficiency | Dual-axis: tokens/s and non-cache tok/s lines vs tool-wait % bar. |
| Tool Call Frequency | Horizontal bar chart of call count by tool name. |
| Cache-Read Ratio | Bar chart of cache ratio % per step with average line. |

### Workflow

- **Step cards** — Vertical card flow with role badge (color per role: blue=user, amber=assistant), step type label, agent badge, duration, tokens, cost, tool count, error count, and text preview with syntax-highlighted code blocks.
- **Detail panel** — Click any card to inspect full step metadata, token breakdown table, and content:
  - Header table with conditional fields: role, agent, mode, model, provider, duration, created/completed timestamps, finish, cost, tool calls, errors, ID, parent ID, session, CWD, root, message ID.
  - Token table: input, output, reasoning, cache read, cache write, total.
  - Content sections: text, reasoning, tool calls (with input/output/error/metadata), patches (hash, files), snapshots.
  - Tool call metadata: tool ID, session ID, model/provider, subagent type, truncated flag, plus generic scalar metadata.

### Analytics

- **Phase detection** — Automatic Boot / Steady / Closeout segmentation based on token and runtime share heuristics.
- **Behavioral insights** — Auto-generated observations:
  - Tool-heavy steps (tool time > 50% of step duration).
  - Cache behavior (median and minimum cache ratio).
  - Slow turns without tool waiting (likely long reasoning).
  - High-token turns near end of trajectory.
  - Context escalation (monotonically increasing token counts).
  - Tool repetition detection (same tool + same target called 3+ times).
- **Behavioral heatmap** — Normalized per-metric heatmap with 6 metrics: cache ratio, tool time share, tok/s, output/input ratio, fresh input tokens, idle gap.
- **Phase timeline** — Stacked horizontal bar showing phase proportions.
- **Tool Duration by Type** — Grouped bar chart of avg / P95 / max duration per tool.
- **Idle Gaps** — Bar chart of inter-step gaps with average line.
- **Per-Step Metrics table** — Sortable dataframe with idx, role, agent, duration, tokens, tok/s, cache %, non-cache, out/in, tool count, tool share %, finish, parts, idle gap.

### Raw Data

Full trajectory JSON viewer (truncated at 500 KB).

## Data Flow

```
parse_steps(raw)           → list[step_dict]     # Normalize trajectory JSON
build_message_metrics(steps) → list[row_dict]    # Per-message metrics for tables/charts
compute_metrics(steps, raw)  → dict              # Aggregate metrics for overview
compute_step_analytics(steps) → list[analytics]  # Per-step derived metrics
detect_phases(analytics)      → list[phase]      # Phase segmentation
generate_insights(analytics, phases, steps) → list[str]  # Behavioral observations
```

### Per-step fields parsed

`index`, `role`, `agent`, `mode`, `model_id`, `provider_id`, `duration`, `cost`, `finish`, `tokens` (total/input/output/reasoning/cache_read/cache_write), `time_created_ms`, `time_completed_ms`, `id`, `parent_id`, `session_id`, `message_id`, `cwd`, `root`, `parts`, `tool_calls`, `tool_call_count`, `error_count`, `has_reasoning`, `text_preview`.

### Per-tool-call fields parsed

`tool_name`, `tool_id`, `status`, `title`, `input`, `output`, `error`, `time_start`, `time_end`, `metadata` (sessionId, model, truncated, plus generic scalars).
