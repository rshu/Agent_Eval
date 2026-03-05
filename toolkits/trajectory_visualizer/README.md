# Trajectory Insight Finder

Gradio-based UI to visualize agent execution trajectories, inspect individual steps, and analyze performance across token usage, tool calls, caching, and behavioral patterns.

## Usage

```bash
# From the project root
python -m toolkits.trajectory_visualizer [--port 7860] [--share]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--port` | `7860` | Server port |
| `--share` | off | Create a public Gradio link |

Once launched, open the UI in your browser at:

```
http://localhost:7860
```

If you specified a custom port (e.g. `--port 8080`), use that port instead. With `--share`, Gradio will also print a temporary public URL (e.g. `https://xxxxx.gradio.live`).

### Loading a Trajectory

Upload a trajectory JSON file using the upload widget and click **Load**.

## Tabs & Features

### Overview

**KPI Strip** — Six cards at a glance:

| Card | Main Value | Sub-Value |
|------|-----------|-----------|
| Steps | Total step count | Assistant steps |
| Wall-Clock | Total duration | P95 step duration |
| Tokens | Total token count | Throughput (tok/s) |
| Tool Success | Success rate % | Total call count |
| Cache Ratio | Average cache hit % | Cache-dominant steps (>=90%) |
| Non-Cache | Fresh input ratio % | Non-cache token count |

**Health Verdict** — Four traffic-light indicators:

| Indicator | Good | Warning | Bad |
|-----------|------|---------|-----|
| Cache Efficiency | >= 60% avg cache ratio | 30-59% | < 30% |
| Tool Success | >= 95% success rate | 80-94% | < 80% |
| Throughput | >= 50 tok/s | 20-49 tok/s | < 20 tok/s |
| Errors | 0 tool failures | 1-2 failures | >= 3 failures |

**Session & Environment** — Model, provider, agent, start/end timestamps, duration, session ID, branch, baseline commit, directory, server version, hostname, platform, Python version, retry attempts.

**Performance & Tokens** — Full metrics table:

| Metric | Description |
|--------|-------------|
| Total steps | Number of trajectory messages |
| Wall-clock time | End-to-end execution time |
| Avg / Median / P95 / Max duration | Step duration distribution |
| Token breakdown | Input, output, reasoning, cache read, cache write |
| Non-cache tokens | Fresh input tokens (not from cache), with percentage |
| Tokens / second | Overall and median throughput |
| Output/Input ratio | How much output per input token |
| Tokens / tool call | Average token cost per tool invocation |
| Tool frequency table | Call count per tool name |
| Agent / model breakdown | Steps per agent and model (if multi-agent) |

**Behavioral Diagnostics** — Behavioral metrics table:

| Metric | Description |
|--------|-------------|
| Multi-tool steps | Assistant steps with 2+ tool calls |
| No-tool steps | Assistant steps with zero tool calls |
| Median / P95 step tokens | Token distribution across assistant steps |
| Cache-dominant steps | Steps where cache ratio >= 90% |
| Tool execution time | Cumulative tool wait time |
| Tool-wait share | Tool time as percentage of total execution |
| Tool duration stats | Avg / P95 / Max per-call duration |

**Output & Agent Stats** — Patch info (lines, size), files changed, additions/deletions, ground truth reference, role breakdown, finish states, tool status breakdown.

**Performance accordion** — Token consumption, step latency, and phase timeline:

| Chart | Type | Description |
|-------|------|-------------|
| Token Usage | Stacked bar | Fresh input, cache read, output, reasoning. Toggle per-step / cumulative. |
| Step Duration | Bar + avg line | Duration per step, color-coded by role/type. Outliers annotated. |
| Phase Timeline | Stacked horizontal bar | Proportional Boot / Steady / Closeout phase lengths. |

**Efficiency accordion** — Context growth, cache behavior, and behavioral heatmap:

| Chart | Type | Description |
|-------|------|-------------|
| Context Growth | Multi-line | Cumulative input, fresh input, and cache read — shows context window pressure. |
| Behavioral Heatmap | Heatmap | Normalized (0-1) per-metric heatmap across all steps (cache ratio, tool time share, tok/s, output/input ratio, fresh input tokens, idle gap). |
| Cache-Read Ratio | Bar + avg line | Cache hit % per step with average reference line. |

**Tools accordion** — Tool usage, throughput, duration:

| Chart | Type | Description |
|-------|------|-------------|
| Per-Step Efficiency | Dual-axis | Tokens/s and non-cache tok/s lines vs tool-wait % bar overlay. |
| Tool Call Frequency | Horizontal bar | Call count per tool name, sorted ascending. |
| Tool Duration by Type | Grouped bar | Avg / P95 / max duration per tool name. |

All charts include:
- **Phase overlays** — Semi-transparent vertical regions showing detected Boot / Steady / Closeout phases.
- **Outlier annotations** — Spikes exceeding 2 standard deviations are labeled automatically.

**Phase Detection** — Automatic segmentation into up to 3 phases:

| Phase | Detection Rule | Meaning |
|-------|---------------|---------|
| Boot | Cumulative tokens < 15% but runtime > 30% | Initialization overhead |
| Steady | Between Boot and Closeout | Main execution |
| Closeout | Trailing stop/end_turn steps with above-average tokens | Final output generation |

Each phase reports its share of total tokens and runtime.

**Behavioral Insights** — Auto-generated observations routed to relevant accordion sections:

| Insight | Trigger | Section |
|---------|---------|---------|
| Tool-heavy steps | Tool time > 50% of step duration | Performance |
| Cache behavior | Reports median and minimum cache ratio | Efficiency |
| Slow turns | Duration > 30s with tool-wait < 30% | Performance |
| High-token turns near end | Largest token steps in final 30% of trajectory | Performance |
| Context escalation | 4+ consecutive non-decreasing token counts | Efficiency |
| Tool repetition | Same tool + input called 3+ times | Tools |

**Per-Step Deep Dive** — Expandable section with message hotspots (top 5 slowest, highest-token, lowest cache-ratio steps) and a full per-step breakdown table.

### Workflow

**Step Cards** — Scrollable vertical flow of step cards, each showing:
- Role badge (blue = user, amber = assistant)
- Step type label (Tool Calls, Reasoning, Text, Error, etc.)
- Agent badge (if present)
- Duration, token count, tool count, error count
- Text preview with syntax-highlighted code blocks

**Filters** — Five-category filter with two-layer logic:
- *Role gate* (AND): **Assistant**, **User** — step must match at least one checked role
- *Content gate* (OR within, AND with role): **Tool Calls**, **Errors**, **Reasoning** — narrows to steps containing selected content types
- **Keyword search** — Filters by text preview, tool names, and tool arguments

**Detail Panel** — Click any step card to inspect (rendered as styled HTML):
- Color-coded header banner with role, step type, and agent
- Metadata table (duration, timestamps, finish reason, IDs, CWD, etc.)
- Content sections: text blocks, reasoning blocks, tool calls (with collapsible input/output/error), patches (hash + files)
- Tool call metadata: tool ID, session, model/provider, subagent type, truncated flag

### Raw Data

Full trajectory JSON viewer (truncated at 500 KB for performance).

## Metrics Reference

### Aggregate Metrics

Computed by `compute_metrics()` from all steps and raw trajectory data.

#### Duration

| Key | Type | Description |
|-----|------|-------------|
| `total_steps` | int | Total number of trajectory messages |
| `total_duration` | float | Sum of all step durations (seconds) |
| `avg_duration` | float | Mean step duration |
| `median_duration` | float | Median step duration |
| `p95_duration` | float | 95th percentile step duration |
| `max_duration` | float | Longest step duration |
| `wall_clock` | float | Wall-clock time from timing metadata |

#### Tokens

| Key | Type | Description |
|-----|------|-------------|
| `tokens.total` | int | Total tokens consumed |
| `tokens.input` | int | Input tokens (includes cache read) |
| `tokens.output` | int | Output tokens (includes reasoning) |
| `tokens.reasoning` | int | Reasoning/thinking tokens |
| `tokens.cache_read` | int | Tokens served from cache |
| `tokens.cache_write` | int | Tokens written to cache |
| `non_cache_tokens` | int | Fresh (non-cached) input tokens |
| `non_cache_ratio` | float | % of input tokens that are fresh |
| `avg_tokens_per_step` | int | Mean tokens per step |
| `tokens_per_second` | int | Overall throughput |
| `median_tokens_per_second` | int | Median per-step throughput |
| `output_input_ratio` | float | Output / input token ratio |
| `median_step_tokens` | int | Median tokens in assistant steps |
| `p95_step_tokens` | int | P95 tokens in assistant steps |
| `avg_cache_ratio` | float | Mean cache hit ratio (%) |
| `cache_dominant_steps` | int | Steps with cache ratio >= 90% |
| `cache_utilization_ratio` | float | cache_read / (cache_read + input) |
| `tokens_per_patch_line` | float | Tokens per line of generated patch |
| `tokens_per_churn_line` | float | Tokens per line of code change |

#### Tools

| Key | Type | Description |
|-----|------|-------------|
| `tool_call_count` | int | Total tool invocations |
| `tool_breakdown` | dict | `{tool_name: count}` |
| `tool_status_breakdown` | dict | `{status: count}` (success, error, etc.) |
| `tool_success` | int | Successful tool calls |
| `tool_fail` | int | Failed tool calls |
| `tool_success_rate` | float | Success percentage |
| `tokens_per_tool` | int | Average tokens per tool call |
| `tool_time_total` | float | Cumulative tool execution time (s) |
| `tool_wait_share` | float | Tool time as % of total duration |
| `avg_tool_duration` | float | Mean tool call duration (s) |
| `p95_tool_duration` | float | P95 tool call duration (s) |
| `max_tool_duration` | float | Longest tool call (s) |
| `multi_tool_steps` | int | Steps with 2+ tool calls |
| `no_tool_assistant_steps` | int | Assistant steps with 0 tool calls |
| `tool_calls_per_min` | float | Tool call rate (per minute) |
| `tool_time_fraction` | float | Tool time / total duration |
| `command_success_rate` | float | Shell command success fraction |
| `command_call_count` | int | Total command invocations |
| `command_failures` | int | Commands with non-zero exit |

#### Efficiency & Behavior

| Key | Type | Description |
|-----|------|-------------|
| `assistant_steps` | int | Count of assistant messages |
| `messages_breakdown` | dict | `{role: count}` |
| `agent_breakdown` | dict | `{agent: count}` (if multi-agent) |
| `model_breakdown` | dict | `{model: count}` (if multi-model) |
| `finish_breakdown` | dict | `{finish_reason: count}` |
| `reasoning_parts` | int | Total reasoning content blocks |
| `text_parts` | int | Total text content blocks |
| `autonomy_ratio` | float | assistant_turns / total_turns |
| `user_turns` | int | Count of user messages |
| `assistant_turns` | int | Count of assistant messages |

#### Output & Patch

| Key | Type | Description |
|-----|------|-------------|
| `has_patch` | bool | Whether a patch was generated |
| `patch_lines` | int | Lines in the generated patch |
| `files_changed` | int | Number of modified files |
| `additions` | int | Lines added |
| `deletions` | int | Lines removed |
| `churn` | int | additions + deletions |
| `net_change` | int | additions - deletions |

#### Timing

| Key | Type | Description |
|-----|------|-------------|
| `time_to_first_token` | float | Latency to first token (s) |
| `output_tokens_per_sec` | float | Output token throughput |
| `time_to_last_token` | float | Latency to complete response (s) |

#### Plan Tracking

| Key | Type | Description |
|-----|------|-------------|
| `plan_items` | int | Initial todo item count |
| `plan_completion_ratio` | float | Fraction of items completed |
| `plan_update_count` | int | Number of plan snapshots |

### Per-Step Analytics

Computed by `compute_step_analytics()` for each trajectory step.

| Field | Type | Description |
|-------|------|-------------|
| `index` | int | Step position |
| `role` | str | "user" or "assistant" |
| `agent` | str | Agent identifier |
| `duration_s` | float | Step duration (seconds) |
| `tool_time_ms` | float | Tool execution time (ms) |
| `tool_time_share` | float | Tool time / step duration (0-1) |
| `tok_total` | int | Total tokens |
| `tok_per_s` | float | Token throughput |
| `cache_ratio` | float | Cache read / total tokens (0-1) |
| `non_cache_tok` | int | Fresh input tokens |
| `out_in_ratio` | float | Output / input token ratio |
| `tool_calls` | int | Tool invocation count |
| `finish` | str | Finish reason |
| `part_mix` | str | Comma-separated part types present |
| `idle_before_s` | float | Gap from previous step (seconds) |

## Data Flow

```
JSON file
  |
  v
load_trajectory(path)            --> raw dict
  |
  v
parse_steps(raw)                 --> list[step_dict]         # Normalize trajectory messages
  |
  +--> build_message_metrics(steps) --> list[row_dict]       # Per-step metrics for charts
  |
  +--> compute_metrics(steps, raw)  --> dict                 # 70+ aggregate metrics
  |
  +--> compute_step_analytics(steps) --> list[analytics]     # Per-step derived metrics
         |
         +--> detect_phases(analytics)     --> list[phase]   # Phase segmentation
         |
         +--> generate_insights(analytics, phases, steps)    # Behavioral observations
         |
         +--> compute_health_verdict(metrics, analytics)     # Traffic-light verdicts
```

## Module Structure

```
trajectory_visualizer/
  __init__.py      # Package marker
  __main__.py      # CLI entry point (argparse)
  data.py          # Loading, parsing, aggregate metrics, markdown formatters
  analytics.py     # Per-step analytics, phase detection, behavioral insights
  charts.py        # Plotly chart builders (9 chart types)
  rendering.py     # Workflow HTML cards, step detail panel, card styling
  styles.py        # Centralized CSS (APP_CSS, WORKFLOW_CSS)
  app.py           # Gradio UI layout, callbacks, KPI builder
  README.md        # This file
```
