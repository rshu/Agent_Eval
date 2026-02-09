# Agent_Eval

A tool for generating coding agent evaluation benchmarks from real pull requests. Given a PR (from GitHub or Gitee) and its patch, it produces three prompt variants at different levels of specificity to test how well coding agents handle varying amounts of context.

## Prompt Versions

| Version | Name | Description |
|---------|------|-------------|
| **v1** | Detailed Issue Description | LLM-rewritten problem statement based on original PR description + ground truth patch |
| **v2** | Weaker / More Vague Issue Description | LLM-generated 1-2 sentence simplification of v1 (always starts with "This issue") |
| **v3** | Detailed Description + Relevant Files List | Same as v1, plus a list of relevant files extracted from the patch |

## Installation

```bash
pip install -r requirements.txt
```

### Requirements

- Python 3.10+
- An API key for Anthropic or OpenAI (used to generate the v2 simplified prompt)

### Environment Variables

Copy `.env.example` to `.env` in the project root and fill in your keys, or set these in your shell:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | LLM provider (`anthropic` or `openai`) |
| `LLM_MODEL` | `gpt-5.2` | Model name |
| `LLM_API_KEY` | — | API key (falls back to `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) |
| `LLM_BASE_URL` | — | Custom API base URL (optional) |
| `LLM_TEMPERATURE` | `0.3` | Sampling temperature |
| `LLM_MAX_TOKENS` | `4096` | Max tokens for the simplified output |
| `GITHUB_TOKEN` | — | GitHub personal access token (increases rate limits) |
| `GITEE_TOKEN` | — | Gitee personal access token |

## Usage

```bash
python -m agent_eval \
  --repo-url https://gitee.com/chinabugotech/hutool \
  --pr-url https://gitee.com/chinabugotech/hutool/pulls/692 \
  --patch path/to/pr_692.patch
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--repo-url` | Yes | Repository URL (GitHub or Gitee) |
| `--pr-url` | Yes | Pull request URL |
| `--patch` | Yes | Path to a local `.patch` file or a URL to download one |
| `--problem-statement` | No | Problem statement text or path to a text file. If omitted, fetched from the PR. |
| `--output-dir` | No | Output directory (default: `Prompts/<ProjectName>/`) |

### Output

The tool writes three files per PR to the output directory:

```
Prompts/Hutool/
  pr_692_v1.md   # Detailed prompt
  pr_692_v2.md   # Simplified prompt
  pr_692_v3.md   # Detailed prompt + relevant file list
```

## Role of the Ground Truth Patch

The `--patch` argument provides the ground truth patch (the actual merged PR diff). It serves two purposes:

1. **Clarifying the issue**: PR descriptions written by developers are often incomplete or vague. The patch reveals the actual scope and intent of the change, which helps when writing or refining the problem statement.
2. **Identifying candidate files**: The patch is parsed to extract the list of changed file paths, which are included in the v3 prompt as recommended files for the agent to focus on.

The patch is **not** included in any of the generated prompts — it is only used as a reference during prompt generation.

## Pipeline

1. **Load original problem statement** from CLI argument, file, or PR API
2. **Load and parse patch** to extract changed file paths (for v3 file list)
3. **Rewrite problem statement** via LLM using original description + ground truth patch (produces the detailed v1 statement)
4. **Generate simplified statement** via LLM from the rewritten statement (produces the vague v2 statement)
5. **Render three prompt templates** (v1, v2, v3)
6. **Write output** to `Prompts/<ProjectName>/pr_<id>_v{1,2,3}.md`

## Example Output

### v1 (Detailed)

```
Task: You are an automated coding agent. Fix/implement the requested change
in the repository based on the PR issue description.

Repo Link: https://gitee.com/chinabugotech/hutool

Problem Statement:
The project currently supports UUID v1 and v4 generation through the IdUtil
utility class, but lacks support for UUID v7. UUID v7 is a newer standard
that produces time-ordered unique identifiers, which is useful for database
keys and distributed systems. There is currently no way to generate UUID v7
values using the existing ID utilities. ...

Deliverable: Generate a standard git-style patch file (unified diff, i.e.,
.patch file) that implements the feature and adds/updates the necessary tests.
```

### v2 (Simplified)

```
Task: You are an automated coding agent. Implement the requested change
described in the issue.

Repo Link: https://gitee.com/chinabugotech/hutool

Problem Statement:
This issue requests adding support for UUID v7 generation to the project
and including some basic tests to verify it works correctly.

Deliverable: Generate a standard git-style patch file (unified diff, i.e.,
.patch file) that implements the feature and adds/updates the necessary tests.
```

### v3 (Detailed + Files)

```
Task: You are an automated coding agent. Fix/implement the requested change
in the repository based on the PR issue description.

Repo Link: https://gitee.com/chinabugotech/hutool

Problem Statement:
The project currently supports UUID v1 and v4 generation through the IdUtil
utility class, but lacks support for UUID v7. ...

Relevant files to update (non-exhaustive but recommended focus):
* hutool-core/src/main/java/org/dromara/hutool/core/data/id/IdUtil.java
* hutool-core/src/main/java/org/dromara/hutool/core/data/id/UUID.java
* hutool-core/src/test/java/org/dromara/hutool/core/util/IdUtilTest.java

Deliverable: Generate a standard git-style patch file (unified diff, i.e.,
.patch file) that implements the feature and adds/updates the necessary tests.
```

## Workspace Management

The `scripts/reset_workspace.sh` script manages the full lifecycle of an evaluation workspace: cloning a repo at the PR's base commit, sanitizing git history to prevent agents from cheating, applying patches, and cleaning up.

### Prerequisites

- Bash (Linux, macOS, or Git Bash on Windows)
- Git

### Subcommands

```bash
# 1. Prepare a workspace — clone, checkout base commit, sanitize history
bash scripts/reset_workspace.sh prepare \
  --repo-url https://github.com/org/repo \
  --base-commit abc123 \
  --workspace ./workspaces/repo \
  --ground-truth ./patches/pr_42.patch

# 2. Reset workspace to the clean base state (no re-clone needed)
bash scripts/reset_workspace.sh reset --workspace ./workspaces/repo

# 3. Apply an agent-produced patch
bash scripts/reset_workspace.sh apply \
  --workspace ./workspaces/repo \
  --patch ./agent_output.patch

# 4. Clean up workspace and metadata
bash scripts/reset_workspace.sh cleanup --workspace ./workspaces/repo
```

### Anti-Cheat Measures

During `prepare`, the original `.git` directory is removed and replaced with a fresh single-commit repository. This prevents agents from accessing git history, remotes, reflogs, or any other information about the original PR:

| Agent cheat attempt | Prevention |
|---|---|
| `git log` | Only one commit ("base"), no useful info |
| `git diff HEAD~1` | Fails — no parent commit |
| `git remote -v` | Empty — no remotes |
| `git reflog` / `git stash` / `git tag` | All empty |
| Read ground truth patch from disk | Stored outside workspace in a hidden sibling `_meta/` directory |

### Evaluation Workflow

```
1. Generate prompts      python -m agent_eval --repo-url ... --pr-url ... --patch ...
2. Prepare workspace     bash scripts/reset_workspace.sh prepare --repo-url ... --base-commit ... --workspace ... --ground-truth ...
3. Run coding agent      (agent works inside the workspace, produces a patch)
4. Reset workspace       bash scripts/reset_workspace.sh reset --workspace ...
5. Apply agent patch     bash scripts/reset_workspace.sh apply --workspace ... --patch agent.patch
6. Compare results       diff the agent's patch against the ground truth
7. Cleanup               bash scripts/reset_workspace.sh cleanup --workspace ...
```

## Project Structure

```
agent_eval/
  __init__.py          # Package version
  __main__.py          # Entry point
  cli.py               # Argument parsing
  renderer.py          # Orchestrator: ties all modules together
  templates.py         # Markdown template rendering for v1/v2/v3
  simplifier.py        # LLM-based problem statement simplification
  patch_parser.py      # Extracts file paths from unified diffs
  fetcher.py           # HTTP fetching for PR descriptions and patches
Prompts/
  <ProjectName>/       # One directory per project (e.g., Hutool/, Pytorch/)
    pr_<id>_v1.md      # Detailed prompt
    pr_<id>_v2.md      # Simplified prompt
    pr_<id>_v3.md      # Detailed prompt + relevant file list
scripts/
  reset_workspace.sh   # Workspace lifecycle management (prepare/reset/apply/cleanup)
.env.example           # Template for environment variables
requirements.txt       # Python dependencies
```
