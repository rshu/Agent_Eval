"""Tests for agent_eval.generate — URL parsing, patch parsing, config validation, renderer."""

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# patch_parser
# ---------------------------------------------------------------------------
from agent_eval.generate.patch_parser import extract_files_from_patch, _unquote_path


class TestExtractFilesNormal:
    """Standard unquoted diff --git lines."""

    def test_single_file(self):
        patch = (
            "diff --git a/foo/bar.py b/foo/bar.py\n"
            "--- a/foo/bar.py\n"
            "+++ b/foo/bar.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["foo/bar.py"]

    def test_multiple_files(self):
        patch = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
        )
        assert extract_files_from_patch(patch) == ["a.py", "b.py"]

    def test_deduplicates(self):
        patch = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n"
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -5 +5 @@\n-a\n+b\n"
        )
        assert extract_files_from_patch(patch) == ["x.py"]

    def test_filters_dev_null(self):
        patch = (
            "diff --git a/gone.py b/gone.py\n"
            "--- a/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-removed\n"
        )
        # /dev/null should be filtered, but diff --git still finds gone.py
        result = extract_files_from_patch(patch)
        assert "/dev/null" not in result


class TestExtractFilesRename:
    """Rename (a-path != b-path)."""

    def test_simple_rename(self):
        patch = (
            "diff --git a/old.py b/new.py\n"
            "--- a/old.py\n"
            "+++ b/new.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["new.py"]

    def test_rename_with_b_slash_in_name(self):
        """Filename contains literal ' b/' — the +++ cross-check should win."""
        patch = (
            "diff --git a/a b/name.txt b/x b/name.txt\n"
            "--- a/a b/name.txt\n"
            "+++ b/x b/name.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        result = extract_files_from_patch(patch)
        assert result == ["x b/name.txt"]


class TestExtractFilesQuoted:
    """Git-quoted paths (spaces, non-ASCII)."""

    def test_quoted_spaces(self):
        patch = (
            'diff --git "a/path/to file.txt" "b/path/to file.txt"\n'
            '--- "a/path/to file.txt"\n'
            '+++ "b/path/to file.txt"\n'
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["path/to file.txt"]

    def test_quoted_octal_utf8(self):
        """Git encodes non-ASCII as octal bytes: é = \\303\\251 in UTF-8."""
        patch = (
            'diff --git "a/caf\\303\\251.txt" "b/caf\\303\\251.txt"\n'
            '--- "a/caf\\303\\251.txt"\n'
            '+++ "b/caf\\303\\251.txt"\n'
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["café.txt"]


class TestExtractFilesBinary:
    """Binary-changed files should be excluded from v3 relevant files."""

    def test_binary_then_text(self):
        """Binary file (no +++) followed by a text file."""
        patch = (
            "diff --git a/new.bin b/new.bin\n"
            "new file mode 100644\n"
            "Binary files /dev/null and b/new.bin differ\n"
            "diff --git a/x.txt b/x.txt\n"
            "--- a/x.txt\n"
            "+++ b/x.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["x.txt"]

    def test_text_then_binary(self):
        """Text file followed by a binary file (no +++)."""
        patch = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/image.png b/image.png\n"
            "new file mode 100644\n"
            "Binary files /dev/null and b/image.png differ\n"
        )
        assert extract_files_from_patch(patch) == ["a.py"]

    def test_multiple_binaries(self):
        """Multiple binary files in a row, none with +++ lines."""
        patch = (
            "diff --git a/a.bin b/a.bin\n"
            "Binary files /dev/null and b/a.bin differ\n"
            "diff --git a/b.bin b/b.bin\n"
            "Binary files /dev/null and b/b.bin differ\n"
            "diff --git a/c.txt b/c.txt\n"
            "--- a/c.txt\n"
            "+++ b/c.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["c.txt"]

    def test_only_binary(self):
        """Patch with only binary files — no +++ lines at all."""
        patch = (
            "diff --git a/img.png b/img.png\n"
            "new file mode 100644\n"
            "Binary files /dev/null and b/img.png differ\n"
        )
        assert extract_files_from_patch(patch) == []

    def test_git_binary_patch_block(self):
        patch = (
            "diff --git a/img.png b/img.png\n"
            "new file mode 100644\n"
            "index 0000000..1111111\n"
            "GIT binary patch\n"
            "literal 1\n"
            "Ac\n"
            "\n"
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["app.py"]


class TestExtractFilesFallback:
    """+++ b/ fallback when no diff --git lines are present."""

    def test_plus_only(self):
        patch = (
            "--- a/hello.txt\n"
            "+++ b/hello.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["hello.txt"]

    def test_plus_quoted(self):
        patch = (
            '--- "a/sp ace.txt"\n'
            '+++ "b/sp ace.txt"\n'
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["sp ace.txt"]


class TestUnquotePath:
    def test_plain(self):
        assert _unquote_path("hello.txt") == "hello.txt"

    def test_basic_escapes(self):
        assert _unquote_path(r'"a\"b\\c"') == 'a"b\\c'

    def test_octal_decode(self):
        # \303\251 → 0xC3 0xA9 → UTF-8 é
        assert _unquote_path(r'"caf\303\251"') == "café"

    def test_tab_newline(self):
        assert _unquote_path(r'"a\tb\nc"') == "a\tb\nc"


class TestExtractFilesNonRenameWithB:
    """Non-rename where the filename itself contains ' b/'."""

    def test_symmetric_b_slash(self):
        patch = (
            "diff --git a/src/a b/c.txt b/src/a b/c.txt\n"
            "--- a/src/a b/c.txt\n"
            "+++ b/src/a b/c.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert extract_files_from_patch(patch) == ["src/a b/c.txt"]


# ---------------------------------------------------------------------------
# fetcher — URL parsing
# ---------------------------------------------------------------------------
from agent_eval.generate.fetcher import (
    is_url,
    parse_repo_url,
    parse_pr_url,
    validate_repo_pr_match,
)


class TestIsUrl:
    def test_lowercase_https(self):
        assert is_url("https://example.com/a.patch") is True

    def test_lowercase_http(self):
        assert is_url("http://example.com/a.patch") is True

    def test_uppercase_scheme(self):
        """HTTPS://... should still be detected as a URL."""
        assert is_url("HTTPS://example.com/a.patch") is True

    def test_mixed_case_scheme(self):
        assert is_url("Https://example.com/a.patch") is True

    def test_local_path(self):
        assert is_url("/tmp/a.patch") is False

    def test_relative_path(self):
        assert is_url("patches/a.patch") is False


class TestParseRepoUrl:
    def test_basic(self):
        assert parse_repo_url("https://github.com/org/repo") == "repo"

    def test_trailing_slash(self):
        assert parse_repo_url("https://github.com/org/repo/") == "repo"

    def test_dot_git(self):
        assert parse_repo_url("https://github.com/org/repo.git") == "repo"

    def test_query_string_stripped(self):
        assert parse_repo_url("https://github.com/org/repo?tab=readme") == "repo"

    def test_fragment_stripped(self):
        assert parse_repo_url("https://github.com/org/repo#section") == "repo"

    def test_query_and_fragment(self):
        assert parse_repo_url("https://github.com/org/repo?a=1#frag") == "repo"

    def test_bare_domain_raises(self):
        """https://github.com/ has no owner/repo — must fail."""
        with pytest.raises(ValueError, match="owner/repo"):
            parse_repo_url("https://github.com/")

    def test_only_owner_raises(self):
        """https://github.com/org/ has only owner, no repo — must fail."""
        with pytest.raises(ValueError, match="owner/repo"):
            parse_repo_url("https://github.com/org/")

    def test_not_a_url_raises(self):
        with pytest.raises(ValueError, match="not a valid URL"):
            parse_repo_url("not-a-url")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="http or https"):
            parse_repo_url("ftp://github.com/org/repo")

    def test_ssh_scheme_rejected(self):
        with pytest.raises(ValueError, match="http or https"):
            parse_repo_url("ssh://gitee.com/org/repo")

    def test_unsupported_host_raises(self):
        """Non-GitHub/Gitee hosts should be rejected."""
        with pytest.raises(ValueError, match="GitHub or Gitee"):
            parse_repo_url("https://gitlab.com/org/repo")

    def test_case_insensitive_host(self):
        """GitHub.com (capital G) should still work."""
        assert parse_repo_url("https://GitHub.com/org/repo") == "repo"

    def test_gitee_accepted(self):
        assert parse_repo_url("https://gitee.com/org/repo") == "repo"

    def test_default_port_443_stripped(self):
        """https://github.com:443/org/repo should work (default HTTPS port)."""
        assert parse_repo_url("https://github.com:443/org/repo") == "repo"

    def test_non_default_port_rejected(self):
        """Explicit non-standard port is not github.com proper."""
        with pytest.raises(ValueError, match="GitHub or Gitee"):
            parse_repo_url("https://github.com:8080/org/repo")

    def test_extra_path_segments_rejected(self):
        """URLs like .../org/repo/tree/main should be rejected (not a repo URL)."""
        with pytest.raises(ValueError, match="extra path segments"):
            parse_repo_url("https://github.com/org/repo/tree/main")

    def test_pr_url_as_repo_url_rejected(self):
        """A PR URL accidentally passed as --repo-url should be rejected."""
        with pytest.raises(ValueError, match="extra path segments"):
            parse_repo_url("https://github.com/org/repo/pull/1")

    def test_dot_git_suffix_stripped(self):
        """repo.git (no extra segments) should strip .git and return 'repo'."""
        assert parse_repo_url("https://github.com/org/repo.git") == "repo"

    def test_bare_dot_git_rejected(self):
        """org/.git should be rejected — repo name is empty after stripping."""
        with pytest.raises(ValueError, match="empty repo name"):
            parse_repo_url("https://github.com/org/.git")

    def test_encoded_slash_in_repo_rejected(self):
        with pytest.raises(ValueError, match="Invalid repo"):
            parse_repo_url("https://github.com/org/re%2Fpo")

    def test_special_char_in_owner_rejected(self):
        with pytest.raises(ValueError, match="Invalid owner"):
            parse_repo_url("https://github.com/o!rg/repo")

    def test_space_in_repo_rejected(self):
        with pytest.raises(ValueError, match="Invalid repo"):
            parse_repo_url("https://github.com/org/repo%20name")

    def test_dot_git_with_extra_segments_rejected(self):
        """repo.git/info has extra segments and should be rejected."""
        with pytest.raises(ValueError, match="extra path segments"):
            parse_repo_url("https://github.com/org/repo.git/info")


class TestParsePrUrl:
    def test_github(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/42")
        assert result == ("github", "owner", "repo", "42")

    def test_gitee(self):
        result = parse_pr_url("https://gitee.com/owner/repo/pulls/99")
        assert result == ("gitee", "owner", "repo", "99")

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_pr_url("https://example.com/foo")

    def test_github_trailing_junk_rejected(self):
        """'pull/1abc' should not parse as PR 1."""
        with pytest.raises(ValueError):
            parse_pr_url("https://github.com/o/r/pull/1abc")

    def test_gitee_trailing_junk_rejected(self):
        with pytest.raises(ValueError):
            parse_pr_url("https://gitee.com/o/r/pulls/99foo")

    def test_github_trailing_slash(self):
        """Browser-copied URL with trailing slash should still parse."""
        result = parse_pr_url("https://github.com/owner/repo/pull/42/")
        assert result == ("github", "owner", "repo", "42")

    def test_github_query_string(self):
        """URL with query string (e.g. ?tab=files) should still parse."""
        result = parse_pr_url("https://github.com/owner/repo/pull/42?tab=files")
        assert result == ("github", "owner", "repo", "42")

    def test_github_fragment(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/42#discussion")
        assert result == ("github", "owner", "repo", "42")

    def test_github_query_and_fragment(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/42?a=1#frag")
        assert result == ("github", "owner", "repo", "42")

    def test_gitee_trailing_slash(self):
        result = parse_pr_url("https://gitee.com/owner/repo/pulls/99/")
        assert result == ("gitee", "owner", "repo", "99")

    def test_gitee_query_string(self):
        result = parse_pr_url("https://gitee.com/owner/repo/pulls/99?tab=diff")
        assert result == ("gitee", "owner", "repo", "99")

    def test_github_case_insensitive_host(self):
        """GitHub.COM should be accepted."""
        result = parse_pr_url("https://GitHub.COM/owner/repo/pull/42")
        assert result == ("github", "owner", "repo", "42")

    def test_gitee_case_insensitive_host(self):
        result = parse_pr_url("https://Gitee.Com/owner/repo/pulls/99")
        assert result == ("gitee", "owner", "repo", "99")

    def test_github_default_port_443(self):
        """https://github.com:443/... should parse like github.com/..."""
        result = parse_pr_url("https://github.com:443/owner/repo/pull/42")
        assert result == ("github", "owner", "repo", "42")

    def test_gitee_default_port_443(self):
        result = parse_pr_url("https://gitee.com:443/owner/repo/pulls/99")
        assert result == ("gitee", "owner", "repo", "99")


# ---------------------------------------------------------------------------
# fetcher — URL cross-validation
# ---------------------------------------------------------------------------


class TestValidateRepoPrMatch:
    def test_matching_urls(self):
        """No error when repo-url and pr-url refer to the same repo."""
        validate_repo_pr_match(
            "https://github.com/org/repo",
            "https://github.com/org/repo/pull/42",
        )

    def test_matching_case_insensitive(self):
        """Owner/repo comparison is case-insensitive."""
        validate_repo_pr_match(
            "https://github.com/Org/Repo",
            "https://github.com/org/repo/pull/1",
        )

    def test_different_repo_raises(self):
        with pytest.raises(ValueError, match="different repositories"):
            validate_repo_pr_match(
                "https://github.com/org/repo-a",
                "https://github.com/org/repo-b/pull/1",
            )

    def test_different_owner_raises(self):
        with pytest.raises(ValueError, match="different repositories"):
            validate_repo_pr_match(
                "https://github.com/alice/repo",
                "https://github.com/bob/repo/pull/1",
            )

    def test_different_platform_raises(self):
        with pytest.raises(ValueError, match="different platforms"):
            validate_repo_pr_match(
                "https://github.com/org/repo",
                "https://gitee.com/org/repo/pulls/1",
            )

    def test_ftp_repo_url_rejected(self):
        """ftp:// repo-url should be rejected before cross-check runs."""
        with pytest.raises(ValueError, match="http or https"):
            validate_repo_pr_match(
                "ftp://github.com/org/repo",
                "https://github.com/org/repo/pull/1",
            )


# ---------------------------------------------------------------------------
# simplifier — config validation & truncation
# ---------------------------------------------------------------------------
from agent_eval.generate.simplifier import _truncate_patch, _get_llm_config, MAX_PATCH_CHARS


class TestTruncatePatch:
    def test_short_untouched(self):
        text = "short patch"
        assert _truncate_patch(text) == text

    def test_long_truncated(self):
        text = "x" * (MAX_PATCH_CHARS + 1000)
        result = _truncate_patch(text)
        assert len(result) < len(text)
        assert result.endswith("[… patch truncated for length …]\n")
        # First MAX_PATCH_CHARS characters preserved
        assert result.startswith("x" * MAX_PATCH_CHARS)


class TestProviderValidation:
    def test_invalid_provider_raises(self, monkeypatch):
        monkeypatch.setenv("GEN_PROVIDER", "badvalue")
        monkeypatch.setenv("GEN_API_KEY", "fake")
        from agent_eval.generate.simplifier import _call_llm
        with pytest.raises(ValueError, match="Invalid GEN_PROVIDER"):
            _call_llm("system", "user")


# ---------------------------------------------------------------------------
# renderer — integration smoke tests
# ---------------------------------------------------------------------------
from agent_eval.generate.renderer import run, load_patch, resolve_output_dir


class TestRendererRun:
    """Smoke-test run() to catch import / wiring errors (no real LLM calls)."""

    def test_bad_repo_url_fails_fast(self):
        """Invalid repo URL should raise before any network/LLM work."""
        with pytest.raises(ValueError, match="not a valid URL"):
            run(repo_url="not-a-url", pr_url="https://github.com/o/r/pull/1", patch="x.patch")

    def test_bad_pr_url_fails_fast(self):
        with pytest.raises(ValueError):
            run(repo_url="https://github.com/o/r", pr_url="bad-url", patch="x.patch")

    def test_load_patch_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_patch("/nonexistent/path.patch")

    def test_load_patch_from_file(self, tmp_path):
        f = tmp_path / "test.patch"
        f.write_text("diff content", encoding="utf-8")
        assert load_patch(str(f)) == "diff content"

    def test_resolve_output_dir_custom(self):
        assert resolve_output_dir("https://github.com/o/r", "/custom/dir") == Path("/custom/dir")

    def test_resolve_output_dir_default(self):
        result = resolve_output_dir("https://github.com/org/my-repo", None)
        assert result == Path("prompt_variants/My-Repo")

    def test_run_missing_patch_before_network(self, monkeypatch):
        """run() should raise FileNotFoundError for a missing patch *before*
        making any network calls (fetch_pr_description)."""
        network_called = False

        def fake_fetch(*_a, **_kw):
            nonlocal network_called
            network_called = True
            return "fake description"

        monkeypatch.setattr(
            "agent_eval.generate.renderer.fetch_pr_description", fake_fetch
        )
        with pytest.raises(FileNotFoundError):
            run(
                repo_url="https://github.com/o/r",
                pr_url="https://github.com/o/r/pull/1",
                patch="/nonexistent/file.patch",
            )
        assert not network_called, "Network was called before patch validation"

    def test_run_repo_pr_mismatch_rejected(self):
        """run() should reject mismatched repo-url and pr-url."""
        with pytest.raises(ValueError, match="different repositories"):
            run(
                repo_url="https://github.com/org/repo-a",
                pr_url="https://github.com/org/repo-b/pull/1",
                patch="/nonexistent/file.patch",
            )

    def test_run_end_to_end_mocked(self, monkeypatch, tmp_path):
        """Full pipeline with mocked network + LLM calls writes v1/v2/v3 files."""
        # Create a local patch file
        patch_file = tmp_path / "test.patch"
        patch_file.write_text(
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n",
            encoding="utf-8",
        )

        # Mock network call
        monkeypatch.setattr(
            "agent_eval.generate.renderer.fetch_pr_description",
            lambda _url: "Fix the bug in foo.py",
        )
        # Mock LLM calls
        monkeypatch.setattr(
            "agent_eval.generate.renderer.rewrite_problem_statement",
            lambda _ps, _patch: "Rewritten: fix the null check in foo.py",
        )
        monkeypatch.setattr(
            "agent_eval.generate.renderer.simplify_problem_statement",
            lambda _ps: "This issue is about a null check bug.",
        )

        out_dir = tmp_path / "output"
        result = run(
            repo_url="https://github.com/org/my-repo",
            pr_url="https://github.com/org/my-repo/pull/42",
            patch=str(patch_file),
            output_dir=str(out_dir),
        )

        assert result == out_dir
        assert (out_dir / "pr_42_v1.md").is_file()
        assert (out_dir / "pr_42_v2.md").is_file()
        assert (out_dir / "pr_42_v3.md").is_file()

        v1 = (out_dir / "pr_42_v1.md").read_text(encoding="utf-8")
        assert "Rewritten: fix the null check" in v1

        v2 = (out_dir / "pr_42_v2.md").read_text(encoding="utf-8")
        assert "null check bug" in v2

        v3 = (out_dir / "pr_42_v3.md").read_text(encoding="utf-8")
        assert "foo.py" in v3  # file list from patch


# ---------------------------------------------------------------------------
# CLI argument wiring
# ---------------------------------------------------------------------------
from agent_eval.cli import main


class TestCLIGenerateWiring:
    """Verify that --mode generate routes args correctly to the handler."""

    def test_generate_missing_args_exits(self):
        """Missing required args should cause a non-zero exit."""
        with pytest.raises(SystemExit):
            main(["--mode", "generate"])

    def test_generate_routes_to_handler(self, monkeypatch):
        """CLI should route to generate handler with parsed args."""
        captured = {}

        def fake_handler(args):
            captured["repo_url"] = args.repo_url
            captured["pr_url"] = args.pr_url
            captured["patch"] = args.patch

        monkeypatch.setattr("agent_eval.generate.command.handler", fake_handler)
        main([
            "--mode", "generate",
            "--repo-url", "https://github.com/org/repo",
            "--pr-url", "https://github.com/org/repo/pull/1",
            "--patch", "test.patch",
        ])
        assert captured["repo_url"] == "https://github.com/org/repo"
        assert captured["pr_url"] == "https://github.com/org/repo/pull/1"
        assert captured["patch"] == "test.patch"
