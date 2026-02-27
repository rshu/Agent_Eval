"""
Embedded evaluation prompt template and formatting helper.
"""

from .exceptions import PromptTemplateError

EVAL_PROMPT_TEMPLATE = r"""You are a strict, detail-oriented code review judge for software-engineering patches.

Your job: evaluate whether the "Generated Patch" correctly addresses the "Issue Statement",
and how it compares to the "Ground Truth Patch" in terms of correctness and completeness.

You must ONLY use the information provided below. Do not assume extra context, files, tests, or runtime.
If the issue statement is vague, judge based on what can be inferred from patches and stated requirements,
and explicitly mark uncertainties.

========================
INPUTS
========================

[Issue Statement]
{ISSUE_STATEMENT}

[Generated Patch]
{GENERATED_PATCH}

[Ground Truth Patch]
{GROUND_TRUTH_PATCH}

(Optional) [Notes / Constraints]
{OPTIONAL_NOTES}

========================
EVALUATION TASK
========================

1) Determine whether the Generated Patch is functionally correct relative to the Issue Statement.
2) Compare the Generated Patch with the Ground Truth Patch:
   - Is it equivalent (same behavior and coverage), partially correct, or incorrect?
   - Does it miss important changes present in Ground Truth?
   - Does it introduce unnecessary changes not in Ground Truth?
3) Score the Generated Patch using criteria A-C and produce a final verdict.

========================
CRITERIA (Score each 0-5)
========================
For each criterion: 0=unacceptable, 1=very poor, 2=poor, 3=acceptable, 4=good, 5=excellent.
Use integer scores only (0, 1, 2, 3, 4, or 5). Interpolate between defined points as needed.

A. Functional Correctness (0-5)
Evaluate whether the patch correctly fixes the bug or implements the requested feature at the semantic level.
Focus on: Does the logic/approach correctly address the root cause of the issue?

Score definitions:
- 5 (Excellent): Clearly addresses the root cause; changes align perfectly with expected semantics; no logical flaws or edge case failures apparent.
- 4 (Good): Addresses the root cause correctly; minor edge cases may be unhandled, but core functionality is sound and reliable.
- 3 (Acceptable): Plausibly fixes the issue; approach is reasonable but may have minor logical gaps or miss some edge cases; still functionally correct for the main use case.
- 2 (Poor): Touches the right area but fix is incomplete or has significant logical issues; may work for some cases but fails for others; approach is partially correct but unreliable.
- 1 (Very Poor): Superficial or speculative fix; likely misses the actual root cause; changes may be related but don't meaningfully address the issue.
- 0 (Unacceptable): Does not address the issue at all, or changes are clearly wrong/contradict the requirement, or introduces new bugs.

B. Completeness & Coverage (0-5)
Evaluate whether the patch handles all required updates across the codebase, including related files, tests, documentation, and edge cases.
Focus on: Does the patch cover all necessary changes implied by the issue and Ground Truth?

Score definitions:
- 5 (Excellent): Covers all essential code-paths, related files (call sites, bindings, initialization if needed), and includes/updates appropriate tests; matches Ground Truth scope or provides an equally complete alternative. All related components are updated.
- 4 (Good): Covers all essential changes but may miss one minor related update (e.g., a single call site, a documentation comment, or a non-critical test case); still comprehensive and usable.
- 3 (Acceptable): Fix exists but misses some secondary updates (e.g., multiple call sites, related conditions, or limited test coverage); still materially improves behavior and covers the primary changes.
- 2 (Poor): Partial fix with notable gaps (e.g., missing several related updates, no tests when clearly needed, or only addresses one of multiple required aspects); incomplete but not broken.
- 1 (Very Poor): Major gaps in coverage (e.g., no tests when required, missing critical related files, or only addresses a small portion of the required changes); patch is significantly incomplete.
- 0 (Unacceptable): Incomplete to the point of being non-functional (won't compile, clearly breaks APIs, or omits essential parts entirely); patch cannot be used as-is.

Note on tests: Tests are required when (1) the issue statement explicitly requests them, (2) the Ground Truth includes tests, or (3) the fix is non-trivial and tests would be standard practice. If none of these apply, lack of tests should not penalize this criterion.

C. Behavioral Equivalence to Ground Truth (0-5)
Evaluate how semantically similar the Generated Patch's behavior is to Ground Truth, focusing on functional equivalence rather than textual similarity.
Focus on: Would both patches produce the same observable behavior and outcomes?

Score definitions:
- 5 (Excellent): Semantically equivalent to Ground Truth (same behavior, same constraints/validation, similar test intent); differences are purely cosmetic, formatting, or clearly safe improvements that don't change behavior.
- 4 (Good): Nearly equivalent with only minor semantic differences (e.g., slightly different error messages, equivalent but different implementation approach, or one additional minor check that doesn't affect core behavior).
- 3 (Acceptable): Achieves the same high-level goal but differs in approach or implementation details; might be missing a minor constraint/test covered by Ground Truth, yet still likely correct and functionally equivalent for the main use cases.
- 2 (Poor): Overlaps significantly with Ground Truth but has notable semantic differences (e.g., handles some cases differently, uses a different algorithm with similar but not identical behavior, or misses a moderate constraint).
- 1 (Very Poor): Overlaps partially with Ground Truth but misses key semantic changes (e.g., only implements half the fix, wrong conditions/logic, wrong API surface, or different error handling that changes behavior).
- 0 (Unacceptable): Diverges from Ground Truth in a way that likely fails the intended behavior, contradicts key aspects of the fix, or implements a fundamentally different solution.

Note: If Ground Truth is unavailable, incomplete, or clearly incorrect, evaluate Criterion C based on whether the Generated Patch's approach is reasonable and complete, rather than strict equivalence. Mark confidence as low in such cases.

========================
REQUIRED OUTPUT FORMAT (STRICT JSON)
========================
Return ONLY a JSON object with the following keys in this exact schema:

{
  "verdict": "PASS" | "PARTIAL" | "FAIL",

  "overall_score": 0-100,
  "scores": {
    "functional_correctness": 0-5,
    "completeness_coverage": 0-5,
    "equivalence_to_ground_truth": 0-5
  },
  "summary": "1-3 sentences max.",
  "key_findings": [
    {"type": "strength" | "weakness" | "risk", "detail": "..." }
  ],
  "diff_against_ground_truth": {
    "missing_from_generated": ["..."],
    "extra_in_generated": ["..."],
    "semantic_differences": ["..."]
  },
  "issue_alignment": {
    "addresses_core_issue": true | false | "uncertain",
    "adds_or_updates_tests": true | false | "uncertain"
  },
  "confidence": 0.0-1.0,
  "recommended_next_checks": [
    "Concrete follow-ups the developer/evaluator should do (e.g., run tests X, inspect file Y)."
  ]
}

========================
SCORING GUIDANCE
========================

INDIVIDUAL SCORE INTERPRETATION (0-5 scale):
Each criterion uses a 0-5 integer scale where:
- 0-1: Unacceptable (patch is fundamentally flawed or incomplete)
- 2: Poor (patch has significant issues but shows some understanding)
- 3: Acceptable (patch works but has notable gaps or issues)
- 4: Good (patch is solid with only minor issues)
- 5: Excellent (patch is exemplary with no significant issues)

OVERALL_SCORE CALCULATION (mandatory formula):
Convert the three 0-5 scores to a 0-100 scale using weighted average:
  overall_score = round((A * 9) + (B * 7) + (C * 4))

Where:
- A = functional_correctness (0-5), weight: 45% (9 points per point)
- B = completeness_coverage (0-5), weight: 35% (7 points per point)
- C = equivalence_to_ground_truth (0-5), weight: 20% (4 points per point)

This yields a score from 0-100 where:
- Maximum: (5 * 9) + (5 * 7) + (5 * 4) = 45 + 35 + 20 = 100
- Minimum: (0 * 9) + (0 * 7) + (0 * 4) = 0

VERDICT RULES (apply in order, first match wins):
The verdict should align with both individual scores and overall_score:

1. FAIL if any of:
   - A <= 1 (functionally incorrect or doesn't address issue)
   - overall_score <= 30 (very poor overall performance)
   - Patch introduces breaking changes (won't compile/run)
   - Patch clearly contradicts issue intent

2. PASS if all of:
   - A >= 4 AND B >= 4 AND C >= 3 (all criteria meet high standards)
   - overall_score >= 70 (good to excellent overall)
   - No major semantic conflicts with Ground Truth

3. PARTIAL if:
   - A >= 2 (at least directionally correct)
   - overall_score between 31-69 (acceptable to good range)
   - Not FAIL (doesn't meet FAIL conditions)
   - Either B < 4 OR C < 3 (incomplete or mismatched with Ground Truth)

4. Default to PARTIAL if none of the above apply (edge cases).

VERDICT AND SCORE ALIGNMENT:
- FAIL: overall_score typically 0-30, A typically 0-1
- PARTIAL: overall_score typically 31-69, A typically 2-3
- PASS: overall_score typically 70-100, A typically 4-5

Note: Verdict should be determined primarily by the verdict rules above, with overall_score serving as a consistency check.

CONFIDENCE SCORING:
- 1.0: High confidence - all information is clear, patches are well-formed, issue is unambiguous.
- 0.8-0.9: Good confidence - minor ambiguities but sufficient information to evaluate.
- 0.6-0.7: Moderate confidence - some missing context or ambiguities that affect evaluation.
- 0.4-0.5: Low confidence - significant missing context, vague issue statement, or unclear patches.
- 0.0-0.3: Very low confidence - cannot reliably evaluate due to insufficient or contradictory information.

If uncertain due to missing context, lower confidence accordingly and explicitly mark "uncertain" fields in issue_alignment."""


def format_prompt(
    issue_statement: str,
    generated_patch: str,
    ground_truth_patch: str,
    optional_notes: str = "",
) -> str:
    """Format the evaluation prompt with the given inputs.

    Args:
        issue_statement: The issue/problem description.
        generated_patch: The agent-generated patch content.
        ground_truth_patch: The ground truth patch content.
        optional_notes: Optional additional notes or constraints.

    Returns:
        The fully formatted prompt string.

    Raises:
        PromptTemplateError: If a required placeholder cannot be filled.
    """
    try:
        # Locate every placeholder in the ORIGINAL template before any
        # substitution.  This prevents injection: if user input contains a
        # placeholder token (e.g. issue text includes "{GENERATED_PATCH}"),
        # chained str.replace() would corrupt already-inserted content.
        placeholders = [
            ("{ISSUE_STATEMENT}", issue_statement),
            ("{GENERATED_PATCH}", generated_patch),
            ("{GROUND_TRUTH_PATCH}", ground_truth_patch),
            ("{OPTIONAL_NOTES}", optional_notes),
        ]
        # Find ALL occurrences of each placeholder in the original template
        # so duplicates are handled correctly.
        positions = []
        for token, value in placeholders:
            start = 0
            found = False
            while True:
                idx = EVAL_PROMPT_TEMPLATE.find(token, start)
                if idx == -1:
                    break
                found = True
                positions.append((idx, len(token), value))
                start = idx + len(token)
            if not found:
                raise PromptTemplateError(
                    f"Placeholder {token} not found in template"
                )
        positions.sort()

        parts = []
        prev = 0
        for idx, length, value in positions:
            parts.append(EVAL_PROMPT_TEMPLATE[prev:idx])
            parts.append(value)
            prev = idx + length
        parts.append(EVAL_PROMPT_TEMPLATE[prev:])
        return "".join(parts)
    except PromptTemplateError:
        raise
    except Exception as e:
        raise PromptTemplateError(f"Error formatting prompt: {e}") from e
