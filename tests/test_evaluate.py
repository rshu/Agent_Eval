"""Tests for the evaluate subpackage."""

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from agent_eval.evaluate.prompt_template import format_prompt
from agent_eval.evaluate.evaluator import PatchEvaluator
from agent_eval.evaluate.exceptions import ValidationError, PromptTemplateError


# ===========================================================================
# Prompt template formatting
# ===========================================================================

class TestFormatPrompt:
    """Tests for format_prompt placeholder substitution."""

    def test_basic_substitution(self):
        result = format_prompt(
            issue_statement="fix the bug",
            generated_patch="--- a/f.py\n+++ b/f.py",
            ground_truth_patch="--- a/f.py\n+++ b/f.py\n-old\n+new",
            optional_notes="none",
        )
        assert "fix the bug" in result
        assert "--- a/f.py" in result
        assert "none" in result

    def test_placeholder_in_input_not_expanded(self):
        """If user input contains a placeholder token, it must NOT be
        expanded by a later replacement (injection via chained replace)."""
        malicious_issue = "Issue about {GENERATED_PATCH} handling"
        gen_patch = "INJECTED_CONTENT"

        result = format_prompt(
            issue_statement=malicious_issue,
            generated_patch=gen_patch,
            ground_truth_patch="gt-patch",
            optional_notes="",
        )
        # The literal string "{GENERATED_PATCH}" from the issue should remain
        # as-is in the output — it should NOT be replaced with gen_patch content.
        assert malicious_issue in result
        # The actual generated patch placeholder should be filled once with gen_patch.
        assert "INJECTED_CONTENT" in result

    def test_all_placeholder_tokens_safe(self):
        """Each placeholder token appearing in user input is preserved literally."""
        for token in ("{ISSUE_STATEMENT}", "{GENERATED_PATCH}",
                      "{GROUND_TRUTH_PATCH}", "{OPTIONAL_NOTES}"):
            result = format_prompt(
                issue_statement=f"test {token} here",
                generated_patch="gp",
                ground_truth_patch="gt",
                optional_notes="notes",
            )
            assert f"test {token} here" in result

    def test_empty_inputs(self):
        """Empty strings are valid substitutions."""
        result = format_prompt(
            issue_statement="issue",
            generated_patch="",
            ground_truth_patch="",
            optional_notes="",
        )
        assert "issue" in result

    def test_duplicate_placeholder_both_replaced(self):
        """If the template had duplicate placeholders, both would be replaced."""
        from agent_eval.evaluate import prompt_template as pt
        original = pt.EVAL_PROMPT_TEMPLATE
        try:
            # Temporarily inject a duplicate placeholder
            pt.EVAL_PROMPT_TEMPLATE = (
                "A: {ISSUE_STATEMENT}\nB: {ISSUE_STATEMENT}\n"
                "C: {GENERATED_PATCH}\nD: {GROUND_TRUTH_PATCH}\nE: {OPTIONAL_NOTES}"
            )
            result = format_prompt(
                issue_statement="my issue",
                generated_patch="gp",
                ground_truth_patch="gt",
                optional_notes="notes",
            )
            # Both occurrences must be replaced
            assert result == "A: my issue\nB: my issue\nC: gp\nD: gt\nE: notes"
            # No literal placeholder tokens remain
            assert "{ISSUE_STATEMENT}" not in result
        finally:
            pt.EVAL_PROMPT_TEMPLATE = original


# ===========================================================================
# Score validation
# ===========================================================================

class TestValidateScores:
    """Tests for PatchEvaluator._validate_scores()."""

    def test_correct_formula(self):
        """When overall_score matches the formula, it stays unchanged."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 100,
            "scores": {
                "functional_correctness": 5,
                "completeness_coverage": 5,
                "equivalence_to_ground_truth": 5,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 100

    def test_formula_mismatch_corrected(self):
        """When overall_score doesn't match, it's corrected to computed value."""
        ev = PatchEvaluator()
        # A=1, B=2, C=1 => round(9 + 14 + 4) = 27
        parsed = {
            "overall_score": 18,
            "scores": {
                "functional_correctness": 1,
                "completeness_coverage": 2,
                "equivalence_to_ground_truth": 1,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 27

    def test_non_numeric_scores_no_crash(self):
        """String score values must not crash the formula computation."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 50,
            "scores": {
                "functional_correctness": "5",
                "completeness_coverage": 3,
                "equivalence_to_ground_truth": 2,
            },
        }
        # Must not raise TypeError
        ev._validate_scores(parsed)
        # overall_score should remain unchanged (non-numeric guard bails out)
        assert parsed["overall_score"] == 50

    def test_all_string_scores_no_crash(self):
        """All string scores — formula computation is skipped entirely."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 42,
            "scores": {
                "functional_correctness": "high",
                "completeness_coverage": "medium",
                "equivalence_to_ground_truth": "low",
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 42

    def test_missing_scores_key(self):
        """No scores key — validation is a no-op."""
        ev = PatchEvaluator()
        parsed = {"overall_score": 50}
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 50

    def test_out_of_range_scores_clamped(self):
        """Scores outside 0-5 are clamped before formula computation."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 50,
            "scores": {
                "functional_correctness": 10,
                "completeness_coverage": -1,
                "equivalence_to_ground_truth": 3,
            },
        }
        ev._validate_scores(parsed)
        # Clamped: A=5, B=0, C=3 => round(45 + 0 + 12) = 57
        assert parsed["overall_score"] == 57
        assert 0 <= parsed["overall_score"] <= 100

    def test_scores_not_dict(self):
        """Non-dict scores payload must not crash."""
        ev = PatchEvaluator()
        for bad_scores in (["a", "b"], "bad", 42, None):
            parsed = {"overall_score": 50, "scores": bad_scores}
            ev._validate_scores(parsed)
            # overall_score unchanged — validation bailed out
            assert parsed["overall_score"] == 50

    def test_overall_score_stays_in_range(self):
        """Corrected overall_score must always be 0-100."""
        ev = PatchEvaluator()
        # Max possible after clamping: A=5, B=5, C=5 => 100
        parsed = {
            "overall_score": 0,
            "scores": {
                "functional_correctness": 999,
                "completeness_coverage": 999,
                "equivalence_to_ground_truth": 999,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 100

        # Min possible after clamping: A=0, B=0, C=0 => 0
        parsed2 = {
            "overall_score": 99,
            "scores": {
                "functional_correctness": -10,
                "completeness_coverage": -10,
                "equivalence_to_ground_truth": -10,
            },
        }
        ev._validate_scores(parsed2)
        assert parsed2["overall_score"] == 0

    def test_boolean_scores_not_treated_as_numeric(self):
        """Booleans (isinstance(True, int) is True) must not be used in formula."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 50,
            "scores": {
                "functional_correctness": True,
                "completeness_coverage": False,
                "equivalence_to_ground_truth": 3,
            },
        }
        ev._validate_scores(parsed)
        # overall_score must remain unchanged — booleans bail out of formula
        assert parsed["overall_score"] == 50

    def test_boolean_overall_score_skips_formula(self):
        """Boolean overall_score is not treated as numeric."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": True,
            "scores": {
                "functional_correctness": 5,
                "completeness_coverage": 5,
                "equivalence_to_ground_truth": 5,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] is True

    def test_nan_scores_not_treated_as_numeric(self):
        """NaN scores must not be used in formula (max/min coerces NaN to 5)."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 10,
            "scores": {
                "functional_correctness": float("nan"),
                "completeness_coverage": 3,
                "equivalence_to_ground_truth": 2,
            },
        }
        ev._validate_scores(parsed)
        # overall_score must remain unchanged — NaN bails out
        assert parsed["overall_score"] == 10

    def test_inf_scores_not_treated_as_numeric(self):
        """Inf scores must not be used in formula."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 20,
            "scores": {
                "functional_correctness": float("inf"),
                "completeness_coverage": 3,
                "equivalence_to_ground_truth": 2,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 20

    def test_nan_overall_score_skips_formula(self):
        """NaN overall_score is not treated as numeric."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": float("nan"),
            "scores": {
                "functional_correctness": 5,
                "completeness_coverage": 5,
                "equivalence_to_ground_truth": 5,
            },
        }
        ev._validate_scores(parsed)
        import math
        assert math.isnan(parsed["overall_score"])

    def test_partial_scores_no_formula_rewrite(self):
        """Missing criteria keys must not trigger formula correction."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 85,
            "scores": {"functional_correctness": 5},
        }
        ev._validate_scores(parsed)
        # overall_score must NOT be rewritten to 45 (i.e. treating missing as 0)
        assert parsed["overall_score"] == 85

    def test_two_of_three_criteria_no_formula_rewrite(self):
        """Two out of three criteria present — still skip formula correction."""
        ev = PatchEvaluator()
        parsed = {
            "overall_score": 70,
            "scores": {
                "functional_correctness": 5,
                "completeness_coverage": 4,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 70

    def test_out_of_range_overall_score_corrected(self):
        """overall_score > 100 must be corrected by the formula."""
        ev = PatchEvaluator()
        # A=5, B=5, C=5 → round(45 + 35 + 20) = 100
        parsed = {
            "overall_score": 200,
            "scores": {
                "functional_correctness": 5,
                "completeness_coverage": 5,
                "equivalence_to_ground_truth": 5,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 100

    def test_negative_overall_score_corrected(self):
        """overall_score < 0 must be corrected by the formula."""
        ev = PatchEvaluator()
        # A=1, B=1, C=1 → round(9 + 7 + 4) = 20
        parsed = {
            "overall_score": -50,
            "scores": {
                "functional_correctness": 1,
                "completeness_coverage": 1,
                "equivalence_to_ground_truth": 1,
            },
        }
        ev._validate_scores(parsed)
        assert parsed["overall_score"] == 20


# ===========================================================================
# JSON parsing
# ===========================================================================

class TestParseJson:
    """Tests for PatchEvaluator._parse_json()."""

    def test_direct_json(self):
        ev = PatchEvaluator()
        result = ev._parse_json('{"verdict": "PASS"}')
        assert result == {"verdict": "PASS"}

    def test_markdown_code_block(self):
        ev = PatchEvaluator()
        text = 'Here is the result:\n```json\n{"verdict": "FAIL"}\n```\nDone.'
        result = ev._parse_json(text)
        assert result == {"verdict": "FAIL"}

    def test_embedded_json_object(self):
        ev = PatchEvaluator()
        text = 'The evaluation: {"verdict": "PARTIAL", "score": 50} is done.'
        result = ev._parse_json(text)
        assert result["verdict"] == "PARTIAL"

    def test_invalid_json_raises(self):
        ev = PatchEvaluator()
        with pytest.raises(json.JSONDecodeError):
            ev._parse_json("this is not json at all")

    def test_json_after_noise_braces(self):
        """Valid JSON after earlier non-JSON brace text is still found."""
        ev = PatchEvaluator()
        text = 'noise {not json} then {"verdict": "PASS"}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"

    def test_json_after_multiple_noise_braces(self):
        ev = PatchEvaluator()
        text = 'a{b}c{d}e {"score": 42}'
        result = ev._parse_json(text)
        assert result["score"] == 42

    def test_json_with_trailing_braces(self):
        """Valid JSON followed by text containing braces is still found."""
        ev = PatchEvaluator()
        text = 'prefix {"verdict": "PASS"} suffix {noise}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"

    def test_json_between_noise_braces(self):
        """Valid JSON sandwiched between non-JSON brace text."""
        ev = PatchEvaluator()
        text = 'noise {bad} then {"score": 99} then {also bad}'
        result = ev._parse_json(text)
        assert result["score"] == 99

    def test_multiple_json_objects_prefers_eval_schema(self):
        """When multiple valid JSON dicts exist, prefer one with evaluation keys."""
        ev = PatchEvaluator()
        text = '{"note": "metadata"} {"verdict": "PASS", "overall_score": 80, "scores": {"functional_correctness": 4, "completeness_coverage": 4, "equivalence_to_ground_truth": 4}}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 80

    def test_multiple_json_objects_falls_back_to_first(self):
        """When no candidate has evaluation keys, return the first dict."""
        ev = PatchEvaluator()
        text = '{"a": 1} {"b": 2}'
        result = ev._parse_json(text)
        assert result == {"a": 1}

    def test_nested_eval_keys_not_mistaken_for_top_level(self):
        """Nested dicts with evaluation-like keys must not be selected over
        the real top-level evaluation object."""
        ev = PatchEvaluator()
        text = '{"meta": {"verdict": "not-eval"}} {"verdict": "PASS", "overall_score": 90, "scores": {"functional_correctness": 5, "completeness_coverage": 4, "equivalence_to_ground_truth": 4}}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 90

    def test_nan_in_json_rejected(self):
        """Non-standard NaN token in JSON must be rejected."""
        ev = PatchEvaluator()
        with pytest.raises((json.JSONDecodeError, ValueError)):
            ev._parse_json('{"score": NaN}')

    def test_infinity_in_json_rejected(self):
        """Non-standard Infinity token in JSON must be rejected."""
        ev = PatchEvaluator()
        with pytest.raises((json.JSONDecodeError, ValueError)):
            ev._parse_json('{"score": Infinity}')

    def test_first_object_has_verdict_only_second_is_full_eval(self):
        """When both objects have 'verdict', prefer the one that passes full schema."""
        ev = PatchEvaluator()
        text = '{"verdict": "meta"} {"verdict": "PASS", "overall_score": 90, "scores": {"functional_correctness": 5, "completeness_coverage": 4, "equivalence_to_ground_truth": 4}}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 90

    def test_array_wrapping_does_not_shadow_real_eval(self):
        """A dict inside a JSON array must not shadow the real evaluation object."""
        ev = PatchEvaluator()
        text = '[{"verdict": "meta"}] {"verdict": "PASS", "overall_score": 90, "scores": {"functional_correctness": 5, "completeness_coverage": 4, "equivalence_to_ground_truth": 4}}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 90

    def test_nan_in_outer_object_does_not_yield_nested_candidate(self):
        """When an outer object contains NaN, its nested dicts must not be
        extracted as separate candidates."""
        ev = PatchEvaluator()
        with pytest.raises((json.JSONDecodeError, ValueError)):
            ev._parse_json('{"meta": {"verdict": "x"}, "overall_score": NaN}')

    def test_infinity_in_outer_object_does_not_yield_nested_candidate(self):
        """When an outer object contains Infinity, its nested dicts must not
        be extracted as separate candidates."""
        ev = PatchEvaluator()
        with pytest.raises((json.JSONDecodeError, ValueError)):
            ev._parse_json('{"meta": {"verdict": "x"}, "score": Infinity}')

    def test_trailing_comma_does_not_yield_nested_candidate(self):
        """Malformed outer JSON (trailing comma) must not leak nested dicts."""
        ev = PatchEvaluator()
        # The only valid object here is the outer one, but it has a trailing
        # comma.  The inner {"verdict":"x"} must NOT be extracted.
        with pytest.raises(json.JSONDecodeError):
            ev._parse_json('{"meta": {"verdict": "x"}, "overall_score": 90,}')

    def test_malformed_outer_with_valid_later_object(self):
        """Malformed outer JSON followed by a valid separate object."""
        ev = PatchEvaluator()
        text = '{"bad": {"a": 1},} {"verdict": "PASS", "overall_score": 80}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 80

    def test_equal_key_count_prefers_valid_value_types(self):
        """When two candidates have the same eval-key count, prefer the one
        with valid value types (scores=dict, overall_score=numeric)."""
        ev = PatchEvaluator()
        valid = json.dumps({
            "verdict": "PASS", "overall_score": 90,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 4,
                       "equivalence_to_ground_truth": 4},
        })
        postamble = json.dumps({
            "verdict": "meta", "overall_score": "n/a", "scores": [],
        })
        text = f"result: {valid} note: {postamble}"
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 90
        assert isinstance(result["scores"], dict)

    def test_unclosed_string_does_not_suppress_later_object(self):
        """An unterminated string in a malformed outer object must not
        prevent recovery of a later valid standalone object."""
        ev = PatchEvaluator()
        text = '{"bad": "unterminated} {"verdict":"PASS","overall_score":80}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 80

    def test_equal_quality_tie_prefers_earlier_candidate(self):
        """When two candidates tie on key overlap and value quality,
        the earlier (first) candidate must be selected."""
        ev = PatchEvaluator()
        first = json.dumps({
            "verdict": "PASS", "overall_score": 90,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 4,
                       "equivalence_to_ground_truth": 4},
        })
        second = json.dumps({
            "verdict": "meta", "overall_score": 10,
            "scores": {"some_criterion": 1},
        })
        text = f"result: {first} note: {second}"
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 90

    def test_overflow_float_rejected(self):
        """Numeric literal that overflows to infinity (1e309) must be
        rejected, not silently accepted as inf."""
        ev = PatchEvaluator()
        with pytest.raises((json.JSONDecodeError, ValueError)):
            ev._parse_json('{"overall_score": 1e309}')

    def test_richer_postamble_does_not_beat_valid_eval(self):
        """A trailing metadata object with extra keys (summary, key_findings)
        must not override a valid earlier evaluation object."""
        ev = PatchEvaluator()
        valid_eval = json.dumps({
            "verdict": "PASS", "overall_score": 90,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 4,
                       "equivalence_to_ground_truth": 4},
        })
        postamble = json.dumps({
            "verdict": "meta", "overall_score": 10,
            "scores": {"functional_correctness": 1},
            "summary": "note", "key_findings": [],
        })
        text = f"result: {valid_eval} note: {postamble}"
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 90

    def test_unclosed_string_inner_dict_skipped_for_later_valid(self):
        """A leaked inner dict from an unclosed-string region must not be
        selected when a later object passes schema validation."""
        ev = PatchEvaluator()
        text = '{"bad": "x {"verdict":"x"} more {"verdict":"PASS","overall_score":80,"scores":{"functional_correctness":4,"completeness_coverage":4,"equivalence_to_ground_truth":4}}'
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 80

    def test_empty_scores_metadata_does_not_match_eval_schema(self):
        """A dict with verdict + empty scores must not pass schema check,
        so a later valid eval object is selected instead."""
        ev = PatchEvaluator()
        meta = json.dumps({"verdict": "meta", "scores": {}})
        valid = json.dumps({
            "verdict": "PASS", "overall_score": 95,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 4,
                       "equivalence_to_ground_truth": 4},
        })
        text = f"{meta} {valid}"
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 95

    def test_arbitrary_scores_metadata_does_not_beat_real_eval(self):
        """A dict with verdict + arbitrary score keys (no known criteria)
        must not beat a later valid evaluation with proper schema."""
        ev = PatchEvaluator()
        meta = json.dumps({"verdict": "meta", "scores": {"foo": 1}})
        valid = json.dumps({
            "verdict": "PASS", "overall_score": 95,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 5,
                       "equivalence_to_ground_truth": 5},
        })
        text = f"{meta} {valid}"
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 95

    def test_leaked_inner_fragment_does_not_beat_full_eval(self):
        """A leaked inner object with only overall_score (no scores dict)
        must not beat a later valid evaluation with complete schema."""
        ev = PatchEvaluator()
        # Malformed outer leaks inner fragment via unclosed string
        text = (
            '{"bad": "x '
            '{"verdict":"PASS","overall_score":0} '
            'junk '
            '{"verdict":"PASS","overall_score":88,'
            '"scores":{"functional_correctness":5,'
            '"completeness_coverage":4,'
            '"equivalence_to_ground_truth":4}}'
        )
        result = ev._parse_json(text)
        assert result["overall_score"] == 88
        assert isinstance(result["scores"], dict)

    def test_full_metadata_with_non_enum_verdict_does_not_beat_real_eval(self):
        """Metadata with verdict + overall_score + scores but non-enum verdict
        must not beat a later valid evaluation."""
        ev = PatchEvaluator()
        meta = json.dumps({
            "verdict": "meta", "overall_score": 10,
            "scores": {"foo": 1},
        })
        valid = json.dumps({
            "verdict": "PASS", "overall_score": 95,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 5,
                       "equivalence_to_ground_truth": 5},
        })
        text = f"{meta} {valid}"
        result = ev._parse_json(text)
        assert result["verdict"] == "PASS"
        assert result["overall_score"] == 95

    def test_case_insensitive_verdict_accepted(self):
        """Lowercase/mixed-case PASS/PARTIAL/FAIL should pass schema check."""
        ev = PatchEvaluator()
        for verdict in ("pass", "Pass", "partial", "Partial", "fail", "Fail"):
            obj = {
                "verdict": verdict, "overall_score": 50,
                "scores": {"functional_correctness": 3,
                           "completeness_coverage": 3,
                           "equivalence_to_ground_truth": 3},
            }
            assert ev._is_evaluation_result(obj), f"{verdict} should pass"


# ===========================================================================
# _is_evaluation_result schema validation
# ===========================================================================

class TestIsEvaluationResult:
    """Tests for PatchEvaluator._is_evaluation_result()."""

    def test_valid_full_eval(self):
        ev = PatchEvaluator()
        obj = {
            "verdict": "PASS", "overall_score": 90,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 4,
                       "equivalence_to_ground_truth": 4},
        }
        assert ev._is_evaluation_result(obj) is True

    def test_empty_verdict_rejected(self):
        """Empty string verdict must not pass (doc says non-empty)."""
        ev = PatchEvaluator()
        obj = {
            "verdict": "", "overall_score": 50,
            "scores": {"x": 1},
        }
        assert ev._is_evaluation_result(obj) is False

    def test_non_enum_verdict_rejected(self):
        """Arbitrary string verdict must not pass (only PASS/PARTIAL/FAIL)."""
        ev = PatchEvaluator()
        obj = {
            "verdict": "meta", "overall_score": 50,
            "scores": {"x": 1},
        }
        assert ev._is_evaluation_result(obj) is False

    def test_enum_verdict_arbitrary_scores_rejected(self):
        """Valid verdict + overall_score but only non-criterion score keys
        must not pass — scores must contain at least one known criterion."""
        ev = PatchEvaluator()
        obj = {
            "verdict": "PASS", "overall_score": 10,
            "scores": {"foo": 1},
        }
        assert ev._is_evaluation_result(obj) is False

    def test_enum_verdict_arbitrary_scores_does_not_beat_real_eval(self):
        """An object with enum verdict + arbitrary score keys must not beat
        a later valid evaluation with proper criterion keys."""
        ev = PatchEvaluator()
        bogus = json.dumps({
            "verdict": "PASS", "overall_score": 10,
            "scores": {"foo": 1},
        })
        valid = json.dumps({
            "verdict": "FAIL", "overall_score": 95,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 5,
                       "equivalence_to_ground_truth": 5},
        })
        text = f"{bogus} {valid}"
        result = ev._parse_json(text)
        assert result["verdict"] == "FAIL"
        assert result["overall_score"] == 95

    def test_single_criterion_key_rejected(self):
        """Only one of three criterion keys present must not pass schema."""
        ev = PatchEvaluator()
        obj = {
            "verdict": "PASS", "overall_score": 10,
            "scores": {"functional_correctness": 0},
        }
        assert ev._is_evaluation_result(obj) is False

    def test_single_criterion_key_does_not_beat_full_eval(self):
        """An object with only one criterion key must not beat a later
        full evaluation with all three criterion keys."""
        ev = PatchEvaluator()
        partial = json.dumps({
            "verdict": "PASS", "overall_score": 10,
            "scores": {"functional_correctness": 0},
        })
        full = json.dumps({
            "verdict": "FAIL", "overall_score": 95,
            "scores": {"functional_correctness": 5,
                       "completeness_coverage": 5,
                       "equivalence_to_ground_truth": 5},
        })
        text = f"{partial} {full}"
        result = ev._parse_json(text)
        assert result["verdict"] == "FAIL"
        assert result["overall_score"] == 95

    def test_missing_scores_rejected(self):
        """Object with verdict + overall_score but no scores must not pass."""
        ev = PatchEvaluator()
        obj = {"verdict": "PASS", "overall_score": 50}
        assert ev._is_evaluation_result(obj) is False

    def test_missing_overall_score_rejected(self):
        """Object with verdict + scores but no overall_score must not pass."""
        ev = PatchEvaluator()
        obj = {"verdict": "PASS", "scores": {"x": 1}}
        assert ev._is_evaluation_result(obj) is False


# ===========================================================================
# Input validation
# ===========================================================================

class TestValidateInputs:
    """Tests for PatchEvaluator._validate_inputs()."""

    def test_valid_inputs(self):
        ev = PatchEvaluator()
        ev._validate_inputs("key", "issue", "patch", "gt")

    def test_empty_api_key(self):
        ev = PatchEvaluator()
        with pytest.raises(ValidationError, match="API key"):
            ev._validate_inputs("", "issue", "patch", "gt")

    def test_whitespace_api_key(self):
        ev = PatchEvaluator()
        with pytest.raises(ValidationError, match="API key"):
            ev._validate_inputs("   ", "issue", "patch", "gt")

    def test_empty_issue(self):
        ev = PatchEvaluator()
        with pytest.raises(ValidationError, match="Issue statement"):
            ev._validate_inputs("key", "", "patch", "gt")

    def test_empty_agent_patch(self):
        ev = PatchEvaluator()
        with pytest.raises(ValidationError, match="Agent patch"):
            ev._validate_inputs("key", "issue", "", "gt")

    def test_empty_gt_patch(self):
        ev = PatchEvaluator()
        with pytest.raises(ValidationError, match="Ground truth"):
            ev._validate_inputs("key", "issue", "patch", "")


# ===========================================================================
# evaluate() integration
# ===========================================================================

class TestEvaluateIntegration:
    """Tests for PatchEvaluator.evaluate() with mocked LLM."""

    def _make_result(self, **overrides):
        base = {
            "verdict": "PASS",
            "overall_score": 100,
            "scores": {
                "functional_correctness": 5,
                "completeness_coverage": 5,
                "equivalence_to_ground_truth": 5,
            },
            "summary": "Good patch.",
            "key_findings": [],
            "confidence": 0.9,
        }
        base.update(overrides)
        return json.dumps(base)

    def test_successful_evaluation(self):
        ev = PatchEvaluator()
        mock_client = MagicMock()
        mock_client.call.return_value = self._make_result()

        with patch("agent_eval.evaluate.evaluator.get_api_client",
                    return_value=mock_client):
            result_json, error = ev.evaluate(
                api_key="test-key",
                issue_statement="fix bug",
                model_name="gpt-test",
                base_url=None,
                agent_patch="--- a/f.py\n+++ b/f.py",
                gt_patch="--- a/f.py\n+++ b/f.py\n-old\n+new",
            )

        assert error is None
        parsed = json.loads(result_json)
        assert parsed["verdict"] == "PASS"

    def test_non_numeric_scores_return_usable_result(self):
        """String scores must not cause evaluate() to return an error."""
        ev = PatchEvaluator()
        mock_client = MagicMock()
        mock_client.call.return_value = self._make_result(
            scores={
                "functional_correctness": "5",
                "completeness_coverage": "3",
                "equivalence_to_ground_truth": "2",
            }
        )

        with patch("agent_eval.evaluate.evaluator.get_api_client",
                    return_value=mock_client):
            result_json, error = ev.evaluate(
                api_key="test-key",
                issue_statement="fix bug",
                model_name="gpt-test",
                base_url=None,
                agent_patch="patch",
                gt_patch="gt",
            )

        assert error is None
        parsed = json.loads(result_json)
        assert "verdict" in parsed

    def test_empty_api_response(self):
        ev = PatchEvaluator()
        mock_client = MagicMock()
        mock_client.call.return_value = ""

        with patch("agent_eval.evaluate.evaluator.get_api_client",
                    return_value=mock_client):
            result_json, error = ev.evaluate(
                api_key="test-key",
                issue_statement="fix bug",
                model_name="gpt-test",
                base_url=None,
                agent_patch="patch",
                gt_patch="gt",
            )

        assert error is not None
        assert "No response" in error

    def test_validation_error_returns_message(self):
        ev = PatchEvaluator()
        result_json, error = ev.evaluate(
            api_key="",
            issue_statement="issue",
            model_name="gpt-test",
            base_url=None,
            agent_patch="patch",
            gt_patch="gt",
        )
        assert error is not None
        assert "API key" in error

    def test_non_eval_dict_returns_raw_text(self):
        """A JSON dict without 'verdict' must be returned as raw text,
        not as a successful evaluation result."""
        ev = PatchEvaluator()
        mock_client = MagicMock()
        raw_response = '{"note": "metadata only"}'
        mock_client.call.return_value = raw_response

        with patch("agent_eval.evaluate.evaluator.get_api_client",
                    return_value=mock_client):
            result_json, error = ev.evaluate(
                api_key="test-key",
                issue_statement="fix bug",
                model_name="gpt-test",
                base_url=None,
                agent_patch="patch",
                gt_patch="gt",
            )

        # Should return the raw text (not parsed/formatted JSON)
        assert result_json == raw_response
        assert error is None

    def test_verdict_only_dict_returns_raw_text(self):
        """A JSON dict with only 'verdict' (no scores/overall_score) must be
        returned as raw text, not treated as a valid evaluation."""
        ev = PatchEvaluator()
        mock_client = MagicMock()
        raw_response = '{"verdict": "meta"}'
        mock_client.call.return_value = raw_response

        with patch("agent_eval.evaluate.evaluator.get_api_client",
                    return_value=mock_client):
            result_json, error = ev.evaluate(
                api_key="test-key",
                issue_statement="fix bug",
                model_name="gpt-test",
                base_url=None,
                agent_patch="patch",
                gt_patch="gt",
            )

        # Must not be accepted as a successful evaluation
        assert result_json == raw_response
        assert error is None


# ===========================================================================
# command.py: _read_file, env parsing, handler
# ===========================================================================

class TestReadFile:
    """Tests for evaluate command._read_file."""

    def test_read_local_file(self, tmp_path):
        from agent_eval.evaluate.command import _read_file
        p = tmp_path / "test.patch"
        p.write_text("patch content")
        assert _read_file(str(p)) == "patch content"

    def test_missing_local_file(self, tmp_path):
        from agent_eval.evaluate.command import _read_file
        with pytest.raises(SystemExit):
            _read_file(str(tmp_path / "nonexistent.patch"))

    def test_url_fetch(self, monkeypatch):
        from agent_eval.evaluate import command as cmd
        monkeypatch.setattr(cmd, "is_url", lambda v: True)
        monkeypatch.setattr(cmd, "fetch_patch_from_url",
                            lambda url: "downloaded content")
        result = cmd._read_file("https://example.com/test.patch")
        assert result == "downloaded content"

    def test_url_fetch_failure(self, monkeypatch):
        from agent_eval.evaluate import command as cmd
        monkeypatch.setattr(cmd, "is_url", lambda v: True)
        monkeypatch.setattr(cmd, "fetch_patch_from_url",
                            MagicMock(side_effect=RuntimeError("fail")))
        with pytest.raises(SystemExit):
            cmd._read_file("https://example.com/fail.patch")


class TestEnvParsing:
    """Tests for EVAL_TEMPERATURE / EVAL_MAX_TOKENS error handling."""

    def test_invalid_temperature(self, monkeypatch):
        from agent_eval.evaluate import command as cmd

        monkeypatch.setenv("EVAL_TEMPERATURE", "not-a-number")
        monkeypatch.setenv("EVAL_API_KEY", "test-key")

        args = SimpleNamespace(
            agent_patch="/dev/null",
            gt_patch="/dev/null",
            issue_statement="test issue",
            eval_model=None,
            eval_output=None,
        )
        # Monkeypatch _read_file to avoid needing real files
        monkeypatch.setattr(cmd, "_read_file", lambda p: "content")
        monkeypatch.setattr(cmd, "_resolve_text_or_file", lambda v: v)

        with pytest.raises(SystemExit):
            cmd.handler(args)

    def test_invalid_max_tokens(self, monkeypatch):
        from agent_eval.evaluate import command as cmd

        monkeypatch.setenv("EVAL_TEMPERATURE", "0.3")
        monkeypatch.setenv("EVAL_MAX_TOKENS", "abc")
        monkeypatch.setenv("EVAL_API_KEY", "test-key")

        args = SimpleNamespace(
            agent_patch="/dev/null",
            gt_patch="/dev/null",
            issue_statement="test issue",
            eval_model=None,
            eval_output=None,
        )
        monkeypatch.setattr(cmd, "_read_file", lambda p: "content")
        monkeypatch.setattr(cmd, "_resolve_text_or_file", lambda v: v)

        with pytest.raises(SystemExit):
            cmd.handler(args)

    def _make_args_and_patch(self, monkeypatch):
        from agent_eval.evaluate import command as cmd
        monkeypatch.setenv("EVAL_API_KEY", "test-key")
        args = SimpleNamespace(
            agent_patch="/dev/null",
            gt_patch="/dev/null",
            issue_statement="test issue",
            eval_model=None,
            eval_output=None,
        )
        monkeypatch.setattr(cmd, "_read_file", lambda p: "content")
        monkeypatch.setattr(cmd, "_resolve_text_or_file", lambda v: v)
        return cmd, args

    def test_nan_temperature_rejected(self, monkeypatch):
        cmd, args = self._make_args_and_patch(monkeypatch)
        monkeypatch.setenv("EVAL_TEMPERATURE", "nan")
        with pytest.raises(SystemExit):
            cmd.handler(args)

    def test_inf_temperature_rejected(self, monkeypatch):
        cmd, args = self._make_args_and_patch(monkeypatch)
        monkeypatch.setenv("EVAL_TEMPERATURE", "inf")
        with pytest.raises(SystemExit):
            cmd.handler(args)

    def test_negative_temperature_rejected(self, monkeypatch):
        cmd, args = self._make_args_and_patch(monkeypatch)
        monkeypatch.setenv("EVAL_TEMPERATURE", "-0.5")
        with pytest.raises(SystemExit):
            cmd.handler(args)

    def test_negative_max_tokens_rejected(self, monkeypatch):
        cmd, args = self._make_args_and_patch(monkeypatch)
        monkeypatch.setenv("EVAL_TEMPERATURE", "0.3")
        monkeypatch.setenv("EVAL_MAX_TOKENS", "-1")
        with pytest.raises(SystemExit):
            cmd.handler(args)

    def test_zero_max_tokens_rejected(self, monkeypatch):
        cmd, args = self._make_args_and_patch(monkeypatch)
        monkeypatch.setenv("EVAL_TEMPERATURE", "0.3")
        monkeypatch.setenv("EVAL_MAX_TOKENS", "0")
        with pytest.raises(SystemExit):
            cmd.handler(args)

    def test_non_eval_raw_payload_no_ok_banner(self, monkeypatch, capsys):
        """handler() must not print [ok] Verdict for non-evaluation raw payloads."""
        from agent_eval.evaluate import command as cmd

        monkeypatch.setenv("EVAL_API_KEY", "test-key")
        monkeypatch.setenv("EVAL_TEMPERATURE", "0.3")
        args = SimpleNamespace(
            agent_patch="/dev/null",
            gt_patch="/dev/null",
            issue_statement="test issue",
            eval_model="gpt-test",
            eval_output=None,
        )
        monkeypatch.setattr(cmd, "_read_file", lambda p: "content")
        monkeypatch.setattr(cmd, "_resolve_text_or_file", lambda v: v)

        # Mock evaluator to return raw non-eval JSON, but preserve the
        # real static methods on the class.
        mock_instance = MagicMock()
        mock_instance.evaluate.return_value = ('{"verdict": "meta"}', None)
        mock_cls = MagicMock()
        mock_cls.return_value = mock_instance
        mock_cls._is_evaluation_result = PatchEvaluator._is_evaluation_result
        mock_cls._strict_loads = PatchEvaluator._strict_loads
        monkeypatch.setattr(cmd, "PatchEvaluator", mock_cls)

        cmd.handler(args)
        captured = capsys.readouterr()
        assert "[ok] Verdict:" not in captured.out
        assert "[warn]" in captured.err

    def test_non_dict_json_does_not_crash_handler(self, monkeypatch, capsys):
        """handler() must not crash when result_json is valid JSON but not a dict."""
        from agent_eval.evaluate import command as cmd

        monkeypatch.setenv("EVAL_API_KEY", "test-key")
        monkeypatch.setenv("EVAL_TEMPERATURE", "0.3")
        args = SimpleNamespace(
            agent_patch="/dev/null",
            gt_patch="/dev/null",
            issue_statement="test issue",
            eval_model="gpt-test",
            eval_output=None,
        )
        monkeypatch.setattr(cmd, "_read_file", lambda p: "content")
        monkeypatch.setattr(cmd, "_resolve_text_or_file", lambda v: v)

        mock_instance = MagicMock()
        mock_instance.evaluate.return_value = ("[]", None)
        mock_cls = MagicMock()
        mock_cls.return_value = mock_instance
        mock_cls._is_evaluation_result = PatchEvaluator._is_evaluation_result
        mock_cls._strict_loads = PatchEvaluator._strict_loads
        monkeypatch.setattr(cmd, "PatchEvaluator", mock_cls)

        # Must not raise AttributeError
        cmd.handler(args)
        captured = capsys.readouterr()
        assert "[ok] Verdict:" not in captured.out
        assert "[warn]" in captured.err

    def test_nan_in_raw_payload_no_ok_banner(self, monkeypatch, capsys):
        """handler() must not print [ok] for responses containing NaN tokens."""
        from agent_eval.evaluate import command as cmd

        monkeypatch.setenv("EVAL_API_KEY", "test-key")
        monkeypatch.setenv("EVAL_TEMPERATURE", "0.3")
        args = SimpleNamespace(
            agent_patch="/dev/null",
            gt_patch="/dev/null",
            issue_statement="test issue",
            eval_model="gpt-test",
            eval_output=None,
        )
        monkeypatch.setattr(cmd, "_read_file", lambda p: "content")
        monkeypatch.setattr(cmd, "_resolve_text_or_file", lambda v: v)

        # Raw response with NaN — evaluator returns it as-is (raw fallback)
        raw = '{"verdict":"PASS","overall_score":50,"scores":{"functional_correctness":NaN}}'
        mock_instance = MagicMock()
        mock_instance.evaluate.return_value = (raw, None)
        mock_cls = MagicMock()
        mock_cls.return_value = mock_instance
        mock_cls._is_evaluation_result = PatchEvaluator._is_evaluation_result
        mock_cls._strict_loads = PatchEvaluator._strict_loads
        monkeypatch.setattr(cmd, "PatchEvaluator", mock_cls)

        cmd.handler(args)
        captured = capsys.readouterr()
        assert "[ok] Verdict:" not in captured.out


class TestResolveTextOrFile:
    """Tests for _resolve_text_or_file."""

    def test_reads_md_file(self, tmp_path):
        from agent_eval.evaluate.command import _resolve_text_or_file
        p = tmp_path / "issue.md"
        p.write_text("# Issue\nBug description")
        result = _resolve_text_or_file(str(p))
        assert "Bug description" in result

    def test_returns_text_as_is(self):
        from agent_eval.evaluate.command import _resolve_text_or_file
        result = _resolve_text_or_file("plain text issue")
        assert result == "plain text issue"

    def test_missing_md_file_exits(self, tmp_path):
        """A .md path that doesn't exist must error, not become literal text."""
        from agent_eval.evaluate.command import _resolve_text_or_file
        with pytest.raises(SystemExit):
            _resolve_text_or_file(str(tmp_path / "nonexistent.md"))

    def test_missing_txt_file_exits(self, tmp_path):
        """A .txt path that doesn't exist must error, not become literal text."""
        from agent_eval.evaluate.command import _resolve_text_or_file
        with pytest.raises(SystemExit):
            _resolve_text_or_file(str(tmp_path / "nonexistent.txt"))

    def test_literal_text_ending_with_md_not_rejected(self, capsys):
        """Multi-word issue text ending with .md must be returned as literal
        and emit a warning."""
        from agent_eval.evaluate.command import _resolve_text_or_file
        result = _resolve_text_or_file("Need to update foo.md")
        assert result == "Need to update foo.md"
        captured = capsys.readouterr()
        assert "[warn]" in captured.err

    def test_literal_text_ending_with_txt_not_rejected(self):
        """Multi-word issue text ending with .txt must be returned as literal."""
        from agent_eval.evaluate.command import _resolve_text_or_file
        result = _resolve_text_or_file("Please fix config.txt")
        assert result == "Please fix config.txt"

    def test_bare_md_filename_missing_exits(self):
        """A bare filename like 'issue.md' (no spaces) should error if missing."""
        from agent_eval.evaluate.command import _resolve_text_or_file
        with pytest.raises(SystemExit):
            _resolve_text_or_file("nonexistent_issue.md")

    def test_uppercase_md_file_read(self, tmp_path):
        """Uppercase .MD extension must be recognized and read."""
        from agent_eval.evaluate.command import _resolve_text_or_file
        p = tmp_path / "ISSUE.MD"
        p.write_text("uppercase issue content")
        result = _resolve_text_or_file(str(p))
        assert result == "uppercase issue content"

    def test_uppercase_txt_file_read(self, tmp_path):
        """Uppercase .TXT extension must be recognized and read."""
        from agent_eval.evaluate.command import _resolve_text_or_file
        p = tmp_path / "ISSUE.TXT"
        p.write_text("uppercase txt content")
        result = _resolve_text_or_file(str(p))
        assert result == "uppercase txt content"

    def test_non_utf8_file_exits(self, tmp_path):
        from agent_eval.evaluate.command import _resolve_text_or_file
        p = tmp_path / "bad.md"
        p.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")
        with pytest.raises(SystemExit):
            _resolve_text_or_file(str(p))


# ===========================================================================
# _read_file encoding handling
# ===========================================================================

class TestReadFileEncoding:
    """Tests for _read_file handling of non-UTF8 files."""

    def test_non_utf8_file_exits_cleanly(self, tmp_path):
        from agent_eval.evaluate.command import _read_file
        p = tmp_path / "bad.patch"
        p.write_bytes(b"\xff\xfe\x00 invalid \x80\x81")
        with pytest.raises(SystemExit):
            _read_file(str(p))


# ===========================================================================
# get_api_client validation
# ===========================================================================

class TestGetApiClient:
    """Tests for get_api_client model_name validation."""

    def test_non_string_model_name_raises(self):
        from agent_eval.evaluate.llm_client import get_api_client
        with pytest.raises(ValueError, match="must be a string"):
            get_api_client(None, "api-key")

    def test_int_model_name_raises(self):
        from agent_eval.evaluate.llm_client import get_api_client
        with pytest.raises(ValueError, match="must be a string"):
            get_api_client(42, "api-key")

    def test_explicit_provider_still_validates_model_name(self):
        from agent_eval.evaluate.llm_client import get_api_client
        with pytest.raises(ValueError, match="must be a string"):
            get_api_client(None, "api-key", provider="openai")

    def test_non_string_provider_raises(self):
        from agent_eval.evaluate.llm_client import get_api_client
        with pytest.raises(ValueError, match="provider must be a string"):
            get_api_client("gpt-test", "api-key", provider=42)

    def test_list_provider_raises(self):
        from agent_eval.evaluate.llm_client import get_api_client
        with pytest.raises(ValueError, match="provider must be a string"):
            get_api_client("gpt-test", "api-key", provider=["openai"])
