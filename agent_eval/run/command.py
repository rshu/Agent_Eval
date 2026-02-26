"""Run mode: execute an agent to generate a code patch for a PR issue."""

import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests

from agent_eval.generate.fetcher import is_url, fetch_patch_from_url

from .opencode_client import (
    AgentDidNotRunError,
    BASE_URL,
    check_health,
    cleanup_session,
    create_session,
    print_response,
    send_task,
)
from .model_resolver import resolve_model, choose_server_model
from .git_helpers import (
    git_run,
    setup_starting_point,
    reset_to_baseline,
    restore_repo,
    recover_sanitize_backup,
    decode_backup_dir,
    _remove_sanitize_sidecar,
)
from .patch_utils import (
    get_patch,
    has_repo_changes,
    validate_patch,
    sanitize_prompt,
)
from .trajectory import collect_trajectory, save_trajectory

MAX_RETRIES = 3


def handler(args):
    """Main entry point for run mode."""
    # Validate required args
    for name in ("directory", "prompt_file"):
        if not getattr(args, name, None):
            sys.exit(f"[error] --{name.replace('_', '-')} is required for run mode")

    agent = "build"
    max_retries = MAX_RETRIES

    # ── Validate inputs (before any git mutations) ──

    with open(args.prompt_file) as f:
        prompt = f.read().strip()

    prompt = sanitize_prompt(prompt)
    print("[ok] Prompt sanitized (repo URLs removed)")

    # Derive project/version from prompt file path for default output paths
    # e.g. prompt_variants/Hutool/pr_692_v1.md → project=Hutool, version=pr_692_v1
    prompt_abs = os.path.abspath(args.prompt_file)
    version_stem = os.path.splitext(os.path.basename(prompt_abs))[0]
    project_name = os.path.basename(os.path.dirname(prompt_abs))

    directory = os.path.abspath(args.directory)

    gt_patch_original = args.gt_patch   # preserve original (may be a URL)
    gt_patch_tempfile = None            # track temp file for cleanup

    # Pre-initialize variables used by the finally block so cleanup is safe
    # even if an exception fires before they would normally be assigned.
    original_ref = None
    baseline_commit = None
    mutated_flag = []
    pre_setup_ref = None
    final_patch = ""
    final_session_id = None

    try:
        if args.gt_patch:
            if is_url(args.gt_patch):
                # Download to a temp file so git apply can use it
                print(f"[..] Downloading ground truth patch from URL...")
                try:
                    patch_content = fetch_patch_from_url(args.gt_patch)
                except Exception as e:
                    sys.exit(f"[error] Failed to download ground truth patch: {e}")
                fd, tmp_path = tempfile.mkstemp(suffix=".patch", prefix="gt_")
                with os.fdopen(fd, "w") as f:
                    f.write(patch_content)
                gt_patch_tempfile = tmp_path
                args.gt_patch = tmp_path
                print(f"[ok] Ground truth patch downloaded ({len(patch_content)} bytes)")
            else:
                gt_patch_abs = os.path.abspath(args.gt_patch)
                if not os.path.isfile(gt_patch_abs):
                    print(f"[error] Ground truth patch not found: {gt_patch_abs}")
                    sys.exit(1)

        # Derive repo URL from gt_patch URL for branch fetching when no remote exists
        gt_patch_repo_url = None
        if gt_patch_original and is_url(gt_patch_original):
            m = re.match(r"(https?://[^/]+/[^/]+/[^/]+)", gt_patch_original)
            if m:
                gt_patch_repo_url = m.group(1) + ".git"

        # ── 1) Health check ──

        t_start = time.time()
        health = check_health()

        # ── 2) Resolve model from config ──

        configured_model, configured_name = resolve_model(agent=agent)

        selected_model, provider_label, model_display, model_warning = choose_server_model(
            configured_model,
            preferred_name=configured_name,
            directory=directory,
        )
        if model_warning:
            print(f"[warn] {model_warning}")
        if selected_model:
            print(f"[ok] Model: {provider_label or selected_model['providerID']}:"
                  f"{model_display or selected_model['modelID']}")
        elif provider_label and model_display:
            print(f"[ok] Model: {provider_label}:{model_display} (server default)")
        else:
            print("[ok] Model: server default")

        # ── 3) Setup starting point (always sanitized) ──
        # Record pre-setup ref so the finally block can do basic cleanup even if
        # setup_starting_point() fails partway through (e.g. after checkout but
        # before sanitization).
        pre_setup_ref = git_run(["rev-parse", "--abbrev-ref", "HEAD"],
                                directory, check=False).stdout.strip()
        if pre_setup_ref == "HEAD":
            pre_setup_ref = git_run(["rev-parse", "HEAD"],
                                    directory, check=False).stdout.strip()

        # ── 4) Main retry loop ──

        attempts = []
        final_error = None
        t_session_created = t_start
        t_task_sent = t_start
        t_task_done = t_start

        original_ref, baseline_commit = setup_starting_point(
            directory,
            branch=args.branch,
            gt_patch=args.gt_patch,
            repo_url=gt_patch_repo_url,
            sanitize=True,
            _mutated_flag=mutated_flag,
        )
        # Decode the trusted backup_dir from the encoded original_ref so
        # reset_to_baseline() can pass it to _read_sidecar(), preventing
        # agent-tampered hint files from redirecting the sidecar lookup.
        trusted_backup_dir = decode_backup_dir(original_ref)

        for attempt in range(1, max_retries + 1):
            print(f"\n{'='*40}")
            print(f"[attempt {attempt}/{max_retries}]")
            print(f"{'='*40}")

            patch = ""
            error = None
            abort_retries = False
            t_session_created = time.time()
            t_task_sent = t_session_created
            t_task_done = t_session_created
            session_id = None

            try:
                # Reset to the baseline (starting point) before each retry
                if attempt > 1:
                    print("[..] Resetting repo to baseline...")
                    reset_to_baseline(directory, baseline_commit,
                                      backup_dir=trusted_backup_dir)
                    print(f"[ok] Repo reset to baseline ({baseline_commit[:10]}).")

                # Create session
                session_id = create_session(directory)
                t_session_created = time.time()
                final_session_id = session_id

                # Send the coding task
                t_task_sent = time.time()
                msg = send_task(session_id, prompt, directory,
                                agent=agent, model=selected_model)
                t_task_done = time.time()

                print_response(msg)

                # Check that the agent actually produced changes
                if not has_repo_changes(directory):
                    print("[warn] Agent responded but made no changes to the repo.")
                    patch = ""
                else:
                    patch = get_patch(directory)

            except AgentDidNotRunError as e:
                error = str(e)
                t_task_done = time.time()
                abort_retries = True
                print(f"[error] {e}")

            except Exception as e:
                error = str(e)
                t_task_done = time.time()
                print(f"[error] {e}")

            # Validate the patch
            if abort_retries:
                is_valid, reason = False, "agent did not run"
            elif patch:
                is_valid, reason = validate_patch(patch)
            else:
                is_valid, reason = False, "empty patch"

            t_attempt_end = time.time()

            # Record this attempt
            attempt_record = {
                "attempt": attempt,
                "session_id": session_id,
                "patch_valid": is_valid,
                "patch_validation_reason": reason,
                "patch_length": len(patch),
                "error": error,
                "duration": round(t_attempt_end - (t_session_created if session_id else t_task_sent), 3),
            }
            attempts.append(attempt_record)

            # Collect trajectory for this attempt before any cleanup
            if session_id:
                try:
                    attempt_trajectory = collect_trajectory(
                        session_id=session_id,
                        directory=directory,
                        prompt=prompt,
                        agent=agent,
                        patch=patch,
                        health=health,
                        t_start=t_start,
                        t_session_created=t_session_created,
                        t_task_sent=t_task_sent,
                        t_task_done=t_task_done,
                        t_end=t_attempt_end,
                        error=error,
                        gt_patch_path=gt_patch_original,
                        branch=args.branch,
                        baseline_commit=baseline_commit,
                    )
                    attempt_record["trajectory"] = attempt_trajectory
                except Exception as te:
                    print(f"[warn] Could not collect trajectory for attempt {attempt}: {te}")

            if is_valid:
                print(f"[ok] Patch is valid ({reason}).")
                final_patch = patch
                final_error = None
                break
            else:
                print(f"[warn] Patch invalid: {reason}.")
                final_error = error if error else f"attempt {attempt}: patch invalid — {reason}"
                # Clean up this failed session before retrying
                if session_id:
                    cleanup_session(session_id, directory)
                if abort_retries:
                    print("[error] Non-retryable failure detected; aborting further attempts.")
                    break
                if attempt < max_retries:
                    print(f"[..] Retrying ({attempt}/{max_retries})...")
        else:
            # All retries exhausted
            print(f"\n[error] All {max_retries} attempts failed to produce a valid patch.")

        t_end = time.time()

        # ── 5) Write the patch file ──

        if final_patch:
            output_path = os.path.join(
                os.getcwd(), "generated_patches", "patch",
                project_name, f"{version_stem}.patch")
            output_path = os.path.abspath(output_path)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                f.write(final_patch)
            print(f"[ok] Patch written to {output_path}")

        # ── 6) Save trajectory ──

        # Use the trajectory from the attempt that produced the final result.
        # For success: the valid attempt.  For failure: the last attempt only.
        # Never mix an earlier attempt's conversation/stats with a later
        # attempt's patch/error — that creates a misleading record.
        final_trajectory = None
        if final_patch:
            # Success — find the successful attempt's trajectory
            for a in reversed(attempts):
                if a.get("patch_valid") and "trajectory" in a:
                    final_trajectory = a["trajectory"]
                    break
        else:
            # Failure — use the last attempt's trajectory only
            if attempts and "trajectory" in attempts[-1]:
                final_trajectory = attempts[-1]["trajectory"]

        if final_trajectory is None:
            # Fallback: minimal record if no trajectory could be collected
            final_trajectory = {
                "metadata": {
                    "session_id": final_session_id,
                    "directory": directory,
                    "directory_name": os.path.basename(directory),
                    "agent": agent,
                    "server_url": BASE_URL,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "branch": args.branch,
                    "ground_truth_patch": gt_patch_original,
                    "baseline_commit": baseline_commit,
                    "sanitized": True,
                },
                "input": {"prompt": prompt, "prompt_length": len(prompt)},
                "output": {"patch": final_patch, "has_patch": bool(final_patch), "error": final_error},
            }

        # Inject sanitization flag into metadata
        if "metadata" in final_trajectory:
            final_trajectory["metadata"]["sanitized"] = True

        # Override output with the final result
        final_trajectory["output"] = {
            "patch": final_patch,
            "patch_length": len(final_patch),
            "patch_lines": len(final_patch.splitlines()) if final_patch else 0,
            "has_patch": bool(final_patch),
            "error": final_error,
        }
        final_trajectory["timing"] = {
            "total_duration": round(t_end - t_start, 3),
            "started_at": datetime.fromtimestamp(t_start, tz=timezone.utc).isoformat(),
            "finished_at": datetime.fromtimestamp(t_end, tz=timezone.utc).isoformat(),
        }
        # Strip per-attempt trajectory data to avoid duplication in the attempts list
        clean_attempts = [{k: v for k, v in a.items() if k != "trajectory"}
                          for a in attempts]
        final_trajectory["retry"] = {
            "max_retries": max_retries,
            "total_attempts": len(attempts),
            "attempts": clean_attempts,
        }
        trajectory_path = os.path.join(
            os.getcwd(), "generated_patches", "trajectory",
            project_name, f"{version_stem}.json")
        trajectory_path = os.path.abspath(trajectory_path)
        save_trajectory(final_trajectory, trajectory_path)

    finally:
        # ── 7) Restore repo to original state (guaranteed) ──

        restore_failed = False
        if original_ref is not None and baseline_commit is not None:
            # Setup completed — use full restore_repo logic
            try:
                restore_repo(directory, original_ref, baseline_commit)
            except Exception as e:
                restore_failed = True
                print(f"[error] Failed to restore repo: {e}")
        elif not mutated_flag:
            # Setup failed before any git mutations (e.g. bad gt_patch path,
            # bad branch name on first checkout).  Nothing to undo.
            print("[ok] Setup failed before mutating repo; no cleanup needed")
        else:
            # Setup started mutating but did not complete — best-effort
            # cleanup using pre-setup ref.
            print("[warn] Setup did not complete; attempting basic cleanup...")
            try:
                # Check if sanitization happened.  If so, backup_dir holds
                # the original .git — but it comes from an untrusted sidecar
                # source (no encoded original_ref is available in this path).
                # Do NOT copy it into the repo: a forged backup could contain
                # a .git with malicious hooks that execute on the git checkout
                # below.  The working tree is still cleaned up using whatever
                # .git is currently in place (the sanitized re-init).
                backup_dir = recover_sanitize_backup(directory)
                if backup_dir:
                    print(f"[warn] Sanitization detected but .git not restored "
                          f"(untrusted source).  Manual restore from: {backup_dir}")
                _remove_sanitize_sidecar(directory)

                git_run(["reset", "--hard", "HEAD"], directory, check=False)
                git_run(["clean", "-fd"], directory, check=False)

                # Check if setup left a baseline commit on the current branch.
                head_msg = git_run(["log", "-1", "--format=%s"],
                                   directory, check=False).stdout.strip()
                if head_msg == "baseline: pre-patch starting point (auto-generated)":
                    parent = git_run(["rev-parse", "--verify", "HEAD^"],
                                     directory, check=False).stdout.strip()
                    if parent:
                        git_run(["reset", "--hard", parent], directory)  # critical
                        print(f"[ok] Removed leftover baseline commit; "
                              f"reset to {parent[:10]}")

                current = git_run(["rev-parse", "--abbrev-ref", "HEAD"],
                                  directory, check=False).stdout.strip()
                if current == "HEAD":
                    current = git_run(["rev-parse", "HEAD"],
                                      directory, check=False).stdout.strip()
                if pre_setup_ref and pre_setup_ref != current:
                    git_run(["checkout", pre_setup_ref], directory)  # critical
                    print(f"[ok] Switched back to pre-setup ref: {pre_setup_ref}")
                print("[ok] Basic cleanup done")
            except Exception as e:
                restore_failed = True
                print(f"[error] Basic cleanup failed: {e}")

        # ── 8) Clean up temp patch file ──

        if gt_patch_tempfile and os.path.isfile(gt_patch_tempfile):
            os.remove(gt_patch_tempfile)

        # ── 9) Cleanup final session ──

        if final_session_id and final_patch:
            cleanup_session(final_session_id, directory)

    # ── 10) Exit with appropriate code ──

    if restore_failed:
        print("[error] Exiting with error: repo restore failed")
        sys.exit(2)
    if not final_patch:
        sys.exit(1)
