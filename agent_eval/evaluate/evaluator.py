"""
Patch evaluation logic — LLM-based judge for agent-generated patches.
"""

import json
import logging
import math
import re
from typing import Optional, Tuple

from .exceptions import APIError, PromptTemplateError, ValidationError
from .llm_client import get_api_client
from .prompt_template import format_prompt

logger = logging.getLogger(__name__)

DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 20480


class PatchEvaluator:
    """Evaluate an agent-generated patch against a ground truth patch using an LLM judge."""

    _VALID_VERDICTS = frozenset({"PASS", "PARTIAL", "FAIL"})
    _CRITERION_KEYS = frozenset({
        "functional_correctness",
        "completeness_coverage",
        "equivalence_to_ground_truth",
    })

    def _validate_inputs(
        self,
        api_key: str,
        issue_statement: str,
        agent_patch: str,
        gt_patch: str,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValidationError("API key is required")
        if not issue_statement or not issue_statement.strip():
            raise ValidationError("Issue statement is required")
        if not agent_patch or not agent_patch.strip():
            raise ValidationError("Agent patch content is required")
        if not gt_patch or not gt_patch.strip():
            raise ValidationError("Ground truth patch content is required")

    def _validate_scores(self, parsed: dict) -> None:
        """Log warnings when scores are out of range or the formula is wrong."""
        scores = parsed.get("scores")
        if not isinstance(scores, dict):
            return

        def _is_numeric(v):
            """True for finite int/float, excluding bool and NaN/inf."""
            if isinstance(v, bool):
                return False
            if not isinstance(v, (int, float)):
                return False
            if isinstance(v, float) and not math.isfinite(v):
                return False
            return True

        for name, value in scores.items():
            if not _is_numeric(value):
                logger.warning("Score %s is not a number: %s", name, value)
            elif not (0 <= value <= 5):
                logger.warning("Score %s out of range (0-5): %s", name, value)

        overall = parsed.get("overall_score")
        if not _is_numeric(overall):
            return

        if not all(k in scores for k in self._CRITERION_KEYS):
            return

        a = scores["functional_correctness"]
        b = scores["completeness_coverage"]
        c = scores["equivalence_to_ground_truth"]
        # Guard against non-numeric score values (e.g. "5", True from LLM)
        if not _is_numeric(a):
            return
        if not _is_numeric(b):
            return
        if not _is_numeric(c):
            return
        # Clamp criteria to 0-5 before computing to avoid out-of-range overall
        a = max(0, min(5, a))
        b = max(0, min(5, b))
        c = max(0, min(5, c))
        expected = round((a * 9) + (b * 7) + (c * 4))
        if abs(overall - expected) > 1:
            parsed["overall_score"] = expected

    @staticmethod
    def _is_evaluation_result(obj: dict) -> bool:
        """Return True if *obj* has the structural shape of an evaluation.

        Requires **all** of:
        - ``verdict``: one of ``PASS``, ``PARTIAL``, ``FAIL`` (case-insensitive)
        - ``scores``: a dict containing all three criterion keys
          (``functional_correctness``, ``completeness_coverage``,
          ``equivalence_to_ground_truth``) each with a numeric value
        - ``overall_score``: a finite number in 0–100 (non-bool)
        """
        verdict = obj.get("verdict")
        if (not isinstance(verdict, str)
                or verdict.upper() not in PatchEvaluator._VALID_VERDICTS):
            return False
        # scores must contain all three criterion keys with numeric values
        scores = obj.get("scores")
        has_scores = (
            isinstance(scores, dict)
            and all(
                k in scores
                and isinstance(scores[k], (int, float))
                and not isinstance(scores[k], bool)
                for k in PatchEvaluator._CRITERION_KEYS
            )
        )
        v = obj.get("overall_score")
        has_overall = (
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and 0 <= v <= 100
        )
        return has_scores and has_overall

    @staticmethod
    def _find_matching_brace(text: str, start: int) -> int:
        """Return position after the ``}`` matching the ``{`` at *start*.

        Counts brace depth while skipping over JSON string literals so that
        braces inside strings are not counted.  Returns ``-1`` when no
        matching brace is found (unbalanced input).
        """
        depth = 0
        i = start
        while i < len(text):
            c = text[i]
            if c == '"':
                # Skip JSON string (handles backslash escapes)
                i += 1
                while i < len(text):
                    if text[i] == '\\':
                        i += 2
                        continue
                    if text[i] == '"':
                        break
                    i += 1
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return -1

    @staticmethod
    def _strict_loads(text: str) -> object:
        """json.loads that rejects non-standard NaN/Infinity tokens and
        numeric literals that overflow to infinity (e.g. 1e309)."""
        def _reject_constant(c):
            raise ValueError(f"Non-standard JSON token: {c}")
        def _strict_float(s):
            v = float(s)
            if not math.isfinite(v):
                raise ValueError(f"Non-finite float value: {s}")
            return v
        return json.loads(text, parse_constant=_reject_constant,
                          parse_float=_strict_float)

    def _parse_json(self, text: str) -> dict:
        """Try to parse JSON from *text* using several fallback strategies."""
        # 1. Direct parse
        try:
            return self._strict_loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. Markdown code block
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                try:
                    return self._strict_loads(text[start:end].strip())
                except (json.JSONDecodeError, ValueError):
                    pass

        # 3. Try each opening brace using raw_decode, which parses exactly
        # one JSON value and ignores trailing text (including extra braces).
        # Only collect top-level objects — skip any { that falls inside an
        # already-parsed span so nested dicts are never separate candidates.
        # A permissive decoder determines object boundaries (so skip_until
        # always advances past the full span even when the content is
        # invalid), then each found object is re-validated strictly.
        # When raw_decode fails (malformed JSON), brace-depth matching
        # finds the span to skip so inner dicts can't leak out.
        boundary_decoder = json.JSONDecoder()
        candidates = []
        skip_until = 0
        for m in re.finditer(r"\{", text):
            if m.start() < skip_until:
                continue
            try:
                obj, end_idx = boundary_decoder.raw_decode(text, m.start())
                skip_until = end_idx
                if not isinstance(obj, dict):
                    continue
                # Re-validate strictly (rejects NaN/Infinity)
                try:
                    strict_obj = self._strict_loads(text[m.start():end_idx])
                    if isinstance(strict_obj, dict):
                        candidates.append(strict_obj)
                except (json.JSONDecodeError, ValueError):
                    pass
            except json.JSONDecodeError:
                # Malformed outer JSON — find matching } by brace depth
                # so nested dicts inside this span are skipped.
                matched_end = self._find_matching_brace(text, m.start())
                if matched_end >= 0:
                    skip_until = matched_end
                else:
                    # Unbalanced braces (e.g. unterminated string) —
                    # skip just this { so later valid objects can still
                    # be recovered.
                    skip_until = m.start() + 1

        if candidates:
            # Return the first candidate that passes full schema validation
            # (enum verdict + all three criterion scores + numeric overall).
            # This avoids heuristic scoring that can be defeated by
            # trailing metadata objects with extra keys.
            for c in candidates:
                if self._is_evaluation_result(c):
                    return c
            return candidates[0]

        raise json.JSONDecodeError("Could not extract JSON from response", text, 0)

    def evaluate(
        self,
        api_key: str,
        issue_statement: str,
        model_name: str,
        base_url: Optional[str],
        agent_patch: str,
        gt_patch: str,
        optional_notes: str = "",
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        provider: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """Evaluate *agent_patch* against *gt_patch* using an LLM judge.

        All arguments are **string content** — the caller is responsible for
        file I/O.

        Returns:
            ``(result_json_string, error_message_or_None)``
        """
        try:
            self._validate_inputs(api_key, issue_statement, agent_patch, gt_patch)

            prompt = format_prompt(
                issue_statement=issue_statement,
                generated_patch=agent_patch,
                ground_truth_patch=gt_patch,
                optional_notes=optional_notes,
            )

            api_client = get_api_client(model_name, api_key, base_url, provider=provider)
            result = api_client.call(
                prompt=prompt,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            if not result:
                return "", "No response received from API"

            try:
                parsed = self._parse_json(result)
                if not isinstance(parsed, dict):
                    raise ValueError("Response is not a JSON object")
                if not self._is_evaluation_result(parsed):
                    raise ValueError("Response is not a valid evaluation result")
                self._validate_scores(parsed)
                return json.dumps(parsed, indent=2, allow_nan=False), None
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("API response is not valid evaluation JSON: %s", e)
                return result, None

        except ValidationError as e:
            logger.error("Validation error: %s", e)
            return "", str(e)
        except (APIError, PromptTemplateError) as e:
            logger.error("Evaluation error: %s", e)
            return "", str(e)
        except Exception as e:
            logger.error("Unexpected error during evaluation: %s", e, exc_info=True)
            return "", f"Unexpected error: {e}"
