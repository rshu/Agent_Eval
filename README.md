# Agent_Eval

Generate, run, and evaluate coding-agent benchmarks from real pull requests.

```
agent-eval --mode generate   # Create prompt variants from a PR
agent-eval --mode run        # Run a coding agent on a prepared workspace
agent-eval --mode evaluate   # Judge an agent patch against ground truth
```

## Installation

```bash
pip install -e .
# or
pip install -r requirements.txt
```

Requires Python 3.10+.

## Configuration

Copy `.env.example` to `.env` and fill in your keys. Generate and evaluate modes can use **different** LLM providers/models:

### Generate mode (`GEN_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `GEN_PROVIDER` | `openai` | LLM provider (`openai` or `anthropic`) |
| `GEN_MODEL` | `gpt-5.2` | Model name |
| `GEN_API_KEY` | — | API key (**required**) |
| `GEN_BASE_URL` | — | Custom API base URL |
| `GEN_TEMPERATURE` | `0.3` | Sampling temperature |
| `GEN_MAX_TOKENS` | `4096` | Max response tokens |

### Evaluate mode (`EVAL_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `EVAL_PROVIDER` | `openai` | LLM provider (`openai` or `anthropic`) |
| `EVAL_MODEL` | `gpt-5.2` | Judge model name |
| `EVAL_API_KEY` | — | API key (**required**) |
| `EVAL_BASE_URL` | — | Custom API base URL |
| `EVAL_TEMPERATURE` | `0.3` | Sampling temperature |
| `EVAL_MAX_TOKENS` | `20480` | Max response tokens |

### Other tokens

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub personal access token (increases rate limits) |
| `GITEE_TOKEN` | Gitee personal access token |

---

## Mode 1 — Generate

Create three prompt variants from a PR and its ground truth patch.

### Prompt Versions

| Version | Name | Description |
|---------|------|-------------|
| **v1** | Detailed Issue Description | LLM-rewritten problem statement based on original PR description + ground truth patch |
| **v2** | Weaker / More Vague Issue Description | LLM-generated 1-2 sentence simplification of v1 (always starts with "This issue") |
| **v3** | Detailed Description + Relevant Files List | Same as v1, plus a list of relevant files extracted from the patch |

### Usage

```bash
agent-eval --mode generate \
  --repo-url https://gitee.com/chinabugotech/hutool \
  --pr-url https://gitee.com/chinabugotech/hutool/pulls/692 \
  --patch https://gitee.com/chinabugotech/hutool/pulls/692.patch
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--repo-url` | Yes | Repository URL — must be `https://<host>/owner/repo[.git]` where host is `github.com` or `gitee.com` |
| `--pr-url` | Yes | Pull request URL (problem statement is fetched from here) |
| `--patch` | Yes | Path to a local `.patch` file or a URL to download one |
| `--output-dir` | No | Output directory (default: `prompt_variants/<ProjectName>/`) |

### Input Validation

Both `--repo-url` and `--pr-url` are validated before any network or LLM calls are made:

- **Scheme**: only `http://` and `https://` are accepted (case-insensitive); `ftp://`, `ssh://`, etc. are rejected
- **Host**: must be `github.com` or `gitee.com` (case-insensitive; default port `:443`/`:80` is stripped)
- **Path**: `--repo-url` must have exactly two path segments (`owner/repo`); extra segments like `/tree/main` or `/pull/1` are rejected
- **Segments**: owner and repo names must contain only word characters, dots, and hyphens (no percent-encoded or special characters)
- **`.git` suffix**: a trailing `.git` on the repo name is stripped (e.g. `repo.git` → `repo`)
- **Cross-check**: `--repo-url` and `--pr-url` must refer to the same platform **and** the same `owner/repo` (case-insensitive comparison)
- **PR URL normalization**: trailing slashes, query strings, and URL fragments are stripped before parsing, so browser-copied URLs work as-is

### Output

```
prompt_variants/Hutool/
  pr_692_v1.md   # Detailed prompt
  pr_692_v2.md   # Simplified prompt
  pr_692_v3.md   # Detailed prompt + relevant file list
```

### Pipeline

1. **Validate & cross-check URLs** — scheme, host, path structure, segment characters, and repo/PR match (see Input Validation above)
2. **Load patch** from local file or URL (note: URL patches trigger a download at this step)
3. **Fetch problem statement** from the PR via GitHub/Gitee API
4. **Extract file paths** from the loaded patch (for v3 file list); binary files are excluded
5. **Rewrite problem statement** via LLM using original description + ground truth patch (produces v1)
6. **Generate simplified statement** via LLM from the rewritten statement (produces v2)
7. **Render three prompt templates** (v1, v2, v3)
8. **Write output** to `prompt_variants/<ProjectName>/pr_<id>_v{1,2,3}.md`

### Role of the Ground Truth Patch

The `--patch` argument provides the ground truth patch (the actual merged PR diff). It serves two purposes:

1. **Clarifying the issue**: PR descriptions are often incomplete. The patch reveals the actual scope and intent, which helps when writing the problem statement.
2. **Identifying candidate files**: The patch is parsed to extract changed file paths (text files only; binary diffs are excluded), included in the v3 prompt.

The patch is **not** included in any generated prompt — it is only used as a reference during generation. Patches larger than 32 000 characters are truncated before being sent to the LLM.

---

## Mode 2 — Run

Run a coding agent on a prepared workspace to produce a patch. Run mode handles the full lifecycle automatically: branch checkout, git history sanitization, ground truth reverse-apply, agent execution (with up to 3 retries), patch extraction, trajectory recording, and workspace restoration.

### Usage

```bash
agent-eval --mode run \
  -d ./workspaces/repo \
  -f prompt_variants/Hutool/pr_692_v1.md \
  --gt-patch https://gitee.com/chinabugotech/hutool/pulls/692.patch \
  --branch pr_692
```

| Argument | Required | Description |
|----------|----------|-------------|
| `-d`, `--directory` | Yes | Target project directory |
| `-f`, `--prompt-file` | Yes | Prompt file (`.md`) to feed the agent |
| `--branch` | No | Git branch to checkout before starting (fetched from remote or PR ref if not found locally) |
| `--gt-patch` | No | Ground truth patch — local file path **or** URL (reverse-applied to set up the starting point) |

### Run mode (`OPENCODE_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | OpenCode server URL |
| `OPENCODE_SERVER_USERNAME` | `opencode` | HTTP basic auth username |
| `OPENCODE_SERVER_PASSWORD` | — | HTTP basic auth password |
| `OPENCODE_MODEL` | — | Model override (`provider:model`, e.g. `openrouter:anthropic/claude-sonnet-4`) |
| `OPENCODE_CONFIG_PATH` | `~/.config/opencode/config.json` | Path to OpenCode config file |

### Running OpenCode Server

Start the OpenCode server before running `agent-eval --mode run`:

```bash
opencode --print-logs --log-level DEBUG serve --hostname 127.0.0.1 --port 4096
```

The server listens on `http://127.0.0.1:4096` by default. Override the port via the OpenCode config or set `OPENCODE_BASE_URL` to point to a different address.

### OpenCode Configuration

The OpenCode config file (`~/.config/opencode/config.json`) defines LLM providers and model settings. Run mode reads this file to resolve the model for the coding agent.

Example configuration using Zhipu AI:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "Zhipu AI Coding Plan/glm-5",
  "provider": {
    "Zhipu AI Coding Plan": {
      "name": "Zhipu AI Coding Plan",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://api.z.ai/api/coding/paas/v4",
        "apiKey": "{env:ZHIPU_API_KEY}"
      },
      "models": {
        "glm-5": {
          "name": "GLM-5"
        }
      }
    }
  }
}
```

**Model resolution order:**

1. `OPENCODE_MODEL` env var (e.g. `Zhipu AI Coding Plan:GLM-5`)
2. `agent.build.model` field in config file
3. First provider/model found in the `provider` block
4. Server default model

### Output

Patches and trajectories are written to fixed paths derived from the prompt file path:

```
generated_patches/
  patch/Hutool/
    pr_692_v1.patch        # Agent-generated patch
  trajectory/Hutool/
    pr_692_v1.json         # Full trajectory (messages, tool calls, timing, token usage)
```

### Lifecycle

1. **Sanitize prompt** — strip repository URLs to prevent agents from looking up the PR
2. **Health check** — verify the OpenCode server is running
3. **Resolve model** — from env var, config file, or server catalog
4. **Setup starting point** — checkout branch, reverse-apply ground truth patch, sanitize git history, commit baseline
5. **Retry loop** (up to 3 attempts) — create session, send task, collect patch, validate
6. **Write outputs** — patch file and trajectory JSON
7. **Restore repo** — undo all git mutations, restore original `.git` history and branch

### Anti-Cheat Measures

During setup, the original `.git` directory is backed up and replaced with a fresh single-commit repo:

| Agent cheat attempt | Prevention |
|---|---|
| `git log` | Only one commit ("base"), no useful info |
| `git diff HEAD~1` | Fails — no parent commit |
| `git remote -v` | Empty — no remotes |
| `git reflog` / `git stash` / `git tag` | All empty |
| Read ground truth from disk | Patch is applied and removed before agent runs |
| Internal metadata in patch | Sidecar files excluded from git and stripped from output |

### Security Hardening

The run subpackage is hardened against a malicious agent running as the same OS user with full filesystem access. Defense-in-depth layers include:

**Sidecar trust hierarchy** — Metadata (backup location, pre-existing ignored files, file permissions) is stored in a sidecar with a 3-level lookup ordered by trust: (1) explicit `backup_dir` decoded from Python-memory-only `original_ref` (most trusted), (2) `.git/info/sidecar_backup` hint file, (3) in-repo sidecar (least trusted, agent can freely tamper).

**Ignored-file restore gates** — Files restored from backup must pass: `_is_safe_relpath()` string validation (rejects absolute paths, `..` traversals, and `.git/*` internals), `_is_git_tracked()` check (rejects tracked files), and post-`makedirs` realpath boundary verification (catches symlink-escape attacks).

**Backup integrity** — `_lock_backup_dir()` sets sidecar and ignored-file backups read-only. `_is_plausible_backup_dir()` validates prefix, temp-directory parent, and `.git` presence. Untrusted backup sources are never used for `.git` restoration (prevents hook-injection via forged backup).

**Malformed data hardening** — All server responses validated with `isinstance()` before use. Sidecar loader rejects non-dict JSON. Field-level type validation for `backup_dir` (str), `pre_agent_ignored` (list of str), `pre_agent_modes` (dict with `math.isfinite()` guard). Trajectory parser handles non-dict messages, non-list parts, non-dict info payloads. `check_health()` and `create_session()` raise clear `RuntimeError` on unexpected response shapes.

**Permission preservation** — Original file modes recorded in sidecar, restored with setuid/setgid/sticky masking and minimum user-write guarantee. Backup-contents enumeration provides an authoritative complement to the (tamperable) `pre_agent_ignored` list during cleanup.

---

## Mode 3 — Evaluate

Judge an agent-generated patch against the ground truth using an LLM.

### Usage

```bash
agent-eval --mode evaluate \
  --agent-patch generated_patches/patch/Hutool/pr_692_v1.patch \
  --gt-patch https://gitee.com/chinabugotech/hutool/pulls/692.patch \
  --issue-statement prompt_variants/Hutool/pr_692_v1.md \
  --eval-output evaluation_scores/Hutool/pr_692_v1.json
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--agent-patch` | Yes | Path to agent-generated patch file |
| `--gt-patch` | Yes | Path to ground truth patch file |
| `--issue-statement` | Yes | Issue text **or** path to a `.md`/`.txt` file |
| `--eval-model` | No | Override judge model (default: `EVAL_MODEL` env or `gpt-5.2`) |
| `--eval-output` | No | Write JSON result to file (default: print to stdout) |

### Output

A JSON object with verdict, scores, and analysis:

```json
{
  "verdict": "PASS | PARTIAL | FAIL",
  "overall_score": 0-100,
  "scores": {
    "functional_correctness": 0-5,
    "completeness_coverage": 0-5,
    "equivalence_to_ground_truth": 0-5
  },
  "summary": "...",
  "key_findings": [...],
  "confidence": 0.0-1.0
}
```

### Scoring

- **Functional Correctness** (weight 45%): Does the patch correctly address the issue?
- **Completeness & Coverage** (weight 35%): Are all necessary changes present?
- **Behavioral Equivalence** (weight 20%): Is the behavior equivalent to ground truth?
- **Overall score**: `round((A * 9) + (B * 7) + (C * 4))` → 0–100

### Verdict Rules

| Verdict | Condition |
|---------|-----------|
| **FAIL** | Functional correctness ≤ 1, or overall ≤ 30 |
| **PASS** | All scores high (A ≥ 4, B ≥ 4, C ≥ 3) and overall ≥ 70 |
| **PARTIAL** | Everything else |

### Security Hardening

The evaluate subpackage is hardened against malformed, adversarial, or non-standard LLM responses. Defense-in-depth layers include:

**Strict JSON parsing** — `_strict_loads()` rejects non-standard tokens (`NaN`, `Infinity`, `-Infinity`) via `parse_constant` and numeric overflow (e.g. `1e309` → inf) via `parse_float` with `math.isfinite()` guard. Final output uses `json.dumps(allow_nan=False)` as a safety net.

**Multi-strategy JSON extraction** — LLM responses are parsed via three fallback strategies: (1) direct parse, (2) markdown code-block extraction, (3) per-brace `raw_decode` with skip-tracking. Nested objects inside already-parsed spans are skipped (`skip_until`). Malformed outer JSON uses brace-depth matching (`_find_matching_brace`) that respects string escaping to find the span boundary, preventing inner dicts from leaking as separate candidates. Unbalanced braces (unclosed strings) recover gracefully by advancing one character.

**Schema-based candidate selection** — When multiple JSON objects are found, `_is_evaluation_result()` validates structural shape before first-match selection. Requires **all** of: verdict in `{PASS, PARTIAL, FAIL}` (case-insensitive), all three criterion keys (`functional_correctness`, `completeness_coverage`, `equivalence_to_ground_truth`) with numeric values, and `overall_score` as a finite number 0–100 (booleans excluded). Metadata or partial objects cannot pass schema and are never selected over a valid evaluation.

**Prompt template injection prevention** — `format_prompt()` uses position-based substitution: all placeholder positions are found in the original template before any replacement occurs. This prevents user input containing placeholder tokens (e.g. `{GENERATED_PATCH}` inside issue text) from being expanded by later substitutions.

**Score formula validation** — `_validate_scores()` requires all three criterion keys present before running the formula. Non-numeric values (strings, booleans, NaN, inf) bail out safely. Criteria are clamped to 0–5 before computing `overall_score`. Mismatched scores are corrected in-place.

**CLI output guard** — The handler summary banner uses `_strict_loads()` (not permissive `json.loads`) and checks `isinstance(dict)` + `_is_evaluation_result()` before printing `[ok]`. Non-evaluation payloads, non-dict JSON, and NaN-containing responses emit `[warn]` instead.

**Input and environment validation** — API key, issue statement, and both patches are validated non-empty. `--issue-statement` uses a file-vs-text heuristic (extension, path separators, whitespace) with warnings on ambiguous cases. `EVAL_TEMPERATURE` requires `math.isfinite()` and `>= 0`; `EVAL_MAX_TOKENS` requires positive integer. `model_name` and `provider` are type-checked with `isinstance`.

---

## Workspace Management

Run mode (`--mode run`) handles workspace setup, sanitization, and restoration automatically. The `scripts/reset_workspace.sh` script is a standalone alternative for manual workspace management outside of run mode.

### Manual Script (optional)

```bash
# 1. Prepare — clone, checkout base commit, sanitize history
bash scripts/reset_workspace.sh prepare \
  --repo-url https://github.com/org/repo \
  --base-commit abc123 \
  --workspace ./workspaces/repo \
  --ground-truth ./patches/pr_42.patch

# 2. Reset workspace to clean base state
bash scripts/reset_workspace.sh reset --workspace ./workspaces/repo

# 3. Apply an agent-produced patch
bash scripts/reset_workspace.sh apply \
  --workspace ./workspaces/repo \
  --patch ./agent_output.patch

# 4. Clean up workspace and metadata
bash scripts/reset_workspace.sh cleanup --workspace ./workspaces/repo
```

---

## End-to-End Workflow

```
1. Generate prompts      agent-eval --mode generate --repo-url ... --pr-url ... --patch ...
                         → prompt_variants/<Project>/pr_<id>_v{1,2,3}.md

2. Run coding agent      agent-eval --mode run -d ./workspaces/repo -f prompt.md --gt-patch gt.patch --branch pr_<id>
                         → generated_patches/patch/<Project>/pr_<id>_v1.patch
                         → generated_patches/trajectory/<Project>/pr_<id>_v1.json

3. Evaluate result       agent-eval --mode evaluate --agent-patch generated_patches/patch/... --gt-patch gt.patch --issue-statement prompt.md --eval-output evaluation_scores/<Project>/pr_<id>_v1.json
                         → evaluation_scores/<Project>/pr_<id>_v{1,2,3}.json
```

Workspace setup (branch checkout, sanitization, baseline) and teardown (restore) are handled automatically by run mode.

## Project Structure

```
agent_eval/
  __init__.py                # Package version
  __main__.py                # Entry point (python -m agent_eval)
  cli.py                     # Argument parsing & mode routing
  generate/
    command.py               # Generate mode handler
    renderer.py              # Orchestrator: ties all generate modules together
    templates.py             # Markdown template rendering for v1/v2/v3
    simplifier.py            # LLM-based problem statement rewriting & simplification
    patch_parser.py          # Extracts file paths from unified diffs
    fetcher.py               # HTTP fetching for PR descriptions and patches
  run/
    command.py               # Run mode handler (retry loop, lifecycle orchestration)
    opencode_client.py       # OpenCode server HTTP client, session lifecycle, response validation
    model_resolver.py        # Model name resolution (env, config file, server catalog)
    git_helpers.py           # Git lifecycle (checkout, sanitize, baseline, reset, restore, sidecar trust)
    patch_utils.py           # Patch extraction, validation, prompt sanitization
    trajectory.py            # Trajectory collection and recording (messages, tool calls, timing)
  evaluate/
    command.py               # Evaluate mode handler
    evaluator.py             # PatchEvaluator — LLM-based judge
    llm_client.py            # API client factory (OpenAI / Anthropic)
    prompt_template.py       # Embedded evaluation prompt template
    exceptions.py            # Custom exception hierarchy
prompt_variants/             # Output from generate mode
  <ProjectName>/
    pr_<id>_v1.md
    pr_<id>_v2.md
    pr_<id>_v3.md
generated_patches/           # Output from run mode
  patch/
    <ProjectName>/
      pr_<id>_v1.patch
  trajectory/
    <ProjectName>/
      pr_<id>_v1.json
evaluation_scores/           # Output from evaluate mode
  <ProjectName>/
    pr_<id>_v1.json
    pr_<id>_v2.json
    pr_<id>_v3.json
scripts/
  reset_workspace.sh         # Workspace lifecycle (prepare/reset/apply/cleanup)
.env.example                 # Environment variable template
pyproject.toml               # Package metadata & dependencies
```
