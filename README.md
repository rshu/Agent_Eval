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

Run a coding agent on a prepared workspace to produce a patch.

### Usage

```bash
agent-eval --mode run \
  -d ./workspaces/repo \
  -f prompt_variants/Hutool/pr_692_v1.md \
  -o generated_patches/Hutool/pr_692.patch \
  --gt-patch patches/pr_692.patch \
  --branch pr_692
```

| Argument | Required | Description |
|----------|----------|-------------|
| `-d`, `--directory` | Yes | Target project directory |
| `-f`, `--prompt-file` | Yes | Prompt file (`.md`) to feed the agent |
| `-o`, `--output` | No | Output patch path (default: `generated_patches/output.patch`) |
| `-t`, `--trajectory` | No | Save agent trajectory to this JSON file |
| `--branch` | No | Git branch to checkout before starting |
| `--gt-patch` | No | Ground truth patch (reverse-applied to set up the starting point) |

---

## Mode 3 — Evaluate

Judge an agent-generated patch against the ground truth using an LLM.

### Usage

```bash
agent-eval --mode evaluate \
  --agent-patch generated_patches/Hutool/pr_692.patch \
  --gt-patch patches/pr_692.patch \
  --issue-statement prompt_variants/Hutool/pr_692_v1.md \
  --eval-output evaluation_scores/Hutool/pr_692.json
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

---

## Workspace Management

The `scripts/reset_workspace.sh` script manages the full lifecycle of an evaluation workspace: cloning a repo at the PR's base commit, sanitizing git history to prevent agents from cheating, applying patches, and cleaning up.

### Subcommands

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

### Anti-Cheat Measures

During `prepare`, the original `.git` directory is replaced with a fresh single-commit repo:

| Agent cheat attempt | Prevention |
|---|---|
| `git log` | Only one commit ("base"), no useful info |
| `git diff HEAD~1` | Fails — no parent commit |
| `git remote -v` | Empty — no remotes |
| `git reflog` / `git stash` / `git tag` | All empty |
| Read ground truth from disk | Stored outside workspace in a hidden `_meta/` directory |

---

## End-to-End Workflow

```
1. Generate prompts      agent-eval --mode generate --repo-url ... --pr-url ... --patch ...
                         → prompt_variants/<Project>/pr_<id>_v{1,2,3}.md
2. Prepare workspace     bash scripts/reset_workspace.sh prepare ...
3. Run coding agent      agent-eval --mode run -d ./workspaces/repo -f prompt.md -o generated_patches/<Project>/pr_<id>.patch
                         → generated_patches/<Project>/pr_<id>.patch
4. Evaluate result       agent-eval --mode evaluate --agent-patch generated_patches/... --gt-patch gt.patch --issue-statement prompt.md --eval-output evaluation_scores/<Project>/pr_<id>.json
                         → evaluation_scores/<Project>/pr_<id>.json
5. Cleanup               bash scripts/reset_workspace.sh cleanup --workspace ./workspaces/repo
```

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
    command.py               # Run mode handler
    opencode_client.py       # OpenCode server API client
    model_resolver.py        # Model name resolution
    git_helpers.py           # Git operations (checkout, patch apply)
    patch_utils.py           # Patch file utilities
    trajectory.py            # Agent trajectory recording
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
  <ProjectName>/
    pr_<id>.patch
evaluation_scores/           # Output from evaluate mode
  <ProjectName>/
    pr_<id>.json
scripts/
  reset_workspace.sh         # Workspace lifecycle (prepare/reset/apply/cleanup)
.env.example                 # Environment variable template
pyproject.toml               # Package metadata & dependencies
```
