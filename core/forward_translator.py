"""Forward Translation module — Round 1 of the audit pipeline.

Translates a natural-language proof into a structured, auditable Lean 4 skeleton.

Design goals:
  1. Avoid one-shot free-form NL → Lean generation.
  2. Force the model to emit a structured translation plan first (JSON).
  3. Render Lean deterministically from that plan.
  4. Inject domain-specific Mathlib references and translation warnings.
  5. Record ambiguities and introduced assumptions explicitly.
  6. Allow an optional compile-and-repair loop without changing semantics.

This module is intentionally conservative: if the model output cannot be parsed,
we return a review-needed result instead of fabricating Lean code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:  # pragma: no cover - real project import
    from core.ai_client import AIClient
except Exception:  # pragma: no cover - test fallback
    AIClient = Any  # type: ignore

from core.mathlib_reference import build_reference_context

AGENTS_DIR = Path(__file__).parent.parent / "agents"

_ALLOWED_STEP_STATUS = {"proved", "sorry", "axiom", "planned"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")

CompileCallback = Callable[[str], tuple[bool, list[str]]]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data classes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class ProofBinder:
    """A theorem binder or hypothesis."""

    name: str
    type_expr: str
    role: str = "binder"  # binder | hypothesis | instance


@dataclass
class ProofStepPlan:
    """A single auditable proof step."""

    step_id: str
    original_text: str = ""
    claim: str = ""
    lean_code: str = ""
    depends_on: list[str] = field(default_factory=list)
    introduces: list[str] = field(default_factory=list)
    reason: str = ""
    status: str = "sorry"  # proved | sorry | axiom | planned


@dataclass
class AmbiguityItem:
    """A source ambiguity that the translator could not fully resolve."""

    phrase: str
    chosen: str
    alternatives: list[str] = field(default_factory=list)
    severity: str = "medium"  # low | medium | high


@dataclass
class ForwardTranslationPlan:
    """Structured intermediate representation for NL → Lean translation."""

    theorem_name: str
    imports: list[str] = field(default_factory=lambda: ["Mathlib"])
    namespace: str = ""
    binders: list[ProofBinder] = field(default_factory=list)
    conclusion: str = ""
    proof_steps: list[ProofStepPlan] = field(default_factory=list)
    final_proof: str = "sorry"
    ambiguities: list[AmbiguityItem] = field(default_factory=list)
    introduced_assumptions: list[str] = field(default_factory=list)
    parse_ok: bool = True
    raw_response: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranslationIssue:
    """A validation or rendering issue detected before/after generation."""

    code: str
    severity: str  # info | warning | fatal
    message: str


@dataclass
class ForwardTranslationResult:
    """Result of the structured forward translation process."""

    plan: ForwardTranslationPlan
    lean_code: str = ""
    issues: list[TranslationIssue] = field(default_factory=list)
    requires_human_review: bool = False
    compiles: Optional[bool] = None
    compile_errors: list[str] = field(default_factory=list)
    repaired_rounds: int = 0
    mathlib_context: str = ""

    @property
    def fatal_issues(self) -> list[TranslationIssue]:
        return [issue for issue in self.issues if issue.severity == "fatal"]

    def summary(self) -> str:
        lines = [
            f"Forward translation: {'review' if self.requires_human_review else 'ok'}",
            f"  theorem: {self.plan.theorem_name or '(missing)'}",
            f"  binders: {len(self.plan.binders)}",
            f"  steps: {len(self.plan.proof_steps)}",
            f"  ambiguities: {len(self.plan.ambiguities)}",
            f"  introduced assumptions: {len(self.plan.introduced_assumptions)}",
        ]
        if self.compiles is not None:
            lines.append(f"  compiles: {self.compiles}")
        for issue in self.issues:
            prefix = {"info": "ℹ", "warning": "⚠", "fatal": "🔴"}.get(issue.severity, "•")
            lines.append(f"  {prefix} {issue.code}: {issue.message}")
        return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _spawn_scoped_client(client: AIClient, system_prompt: str) -> AIClient:
    """Create a fresh client with a dedicated system prompt when possible."""

    try:
        return AIClient(
            provider=client.provider,
            model=client.model,
            system_prompt=system_prompt,
        )
    except Exception:
        return client


def _extract_json_payload(text: str) -> Optional[dict[str, Any]]:
    """Extract a JSON object from raw model output."""

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    text = text.strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            return None
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value).strip()]


def _sanitize_identifier(name: str, default: str) -> str:
    text = (name or "").strip()
    if not text:
        return default
    cleaned = re.sub(r"\s+", "_", text)
    cleaned = re.sub(r"[^A-Za-z0-9_']", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return default
    if cleaned[0].isdigit():
        cleaned = f"{default}_{cleaned}"
    return cleaned


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Plan parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def parse_forward_translation_plan(
    text: str,
    theorem_name: str = "audit_theorem",
) -> ForwardTranslationPlan:
    """Parse the model's JSON plan into a normalized dataclass."""

    payload = _extract_json_payload(text)
    if not payload:
        return ForwardTranslationPlan(
            theorem_name=_sanitize_identifier(theorem_name, "audit_theorem"),
            parse_ok=False,
            raw_response=text,
        )

    plan = ForwardTranslationPlan(
        theorem_name=_sanitize_identifier(
            str(payload.get("theorem_name", theorem_name)),
            _sanitize_identifier(theorem_name, "audit_theorem"),
        ),
        imports=_string_list(payload.get("imports")) or ["Mathlib"],
        namespace=str(payload.get("namespace", "")).strip(),
        conclusion=str(payload.get("conclusion", "")).strip(),
        final_proof=str(payload.get("final_proof", "sorry")).strip() or "sorry",
        introduced_assumptions=_string_list(payload.get("introduced_assumptions")),
        parse_ok=True,
        raw_response=text,
        raw_payload=payload,
    )

    raw_binders = payload.get("binders", [])
    if isinstance(raw_binders, list):
        for item in raw_binders:
            if not isinstance(item, dict):
                continue
            plan.binders.append(
                ProofBinder(
                    name=str(item.get("name", "")).strip(),
                    type_expr=str(item.get("type", item.get("type_expr", ""))).strip(),
                    role=str(item.get("role", "binder")).strip() or "binder",
                )
            )

    raw_ambiguities = payload.get("ambiguities", [])
    if isinstance(raw_ambiguities, list):
        for item in raw_ambiguities:
            if not isinstance(item, dict):
                continue
            plan.ambiguities.append(
                AmbiguityItem(
                    phrase=str(item.get("phrase", "")).strip(),
                    chosen=str(item.get("chosen", "")).strip(),
                    alternatives=_string_list(item.get("alternatives")),
                    severity=str(item.get("severity", "medium")).strip() or "medium",
                )
            )

    raw_steps = payload.get("proof_steps", payload.get("steps", []))
    if isinstance(raw_steps, list):
        for idx, item in enumerate(raw_steps, 1):
            if not isinstance(item, dict):
                continue
            step_id = _sanitize_identifier(str(item.get("step_id", f"s{idx}")), f"s{idx}")
            status = str(item.get("status", "sorry")).strip().lower() or "sorry"
            if status not in _ALLOWED_STEP_STATUS:
                status = "sorry"
            plan.proof_steps.append(
                ProofStepPlan(
                    step_id=step_id,
                    original_text=str(item.get("original_text", "")).strip(),
                    claim=str(item.get("claim", "")).strip(),
                    lean_code=str(item.get("lean_code", "")).rstrip(),
                    depends_on=_string_list(item.get("depends_on")),
                    introduces=_string_list(item.get("introduces")),
                    reason=str(item.get("reason", "")).strip(),
                    status=status,
                )
            )

    return plan


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Plan validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_forward_translation_plan(plan: ForwardTranslationPlan) -> list[TranslationIssue]:
    """Validate plan consistency before rendering Lean."""

    issues: list[TranslationIssue] = []

    if not plan.parse_ok:
        issues.append(
            TranslationIssue(
                code="parse_failed",
                severity="fatal",
                message="Could not parse forward translator JSON output.",
            )
        )
        return issues

    if not plan.conclusion:
        issues.append(
            TranslationIssue(
                code="missing_conclusion",
                severity="fatal",
                message="Translation plan is missing the theorem conclusion.",
            )
        )

    binder_names: list[str] = []
    for binder in plan.binders:
        if not binder.name:
            issues.append(
                TranslationIssue(
                    code="binder_missing_name",
                    severity="fatal",
                    message="A binder is missing its name.",
                )
            )
            continue
        if not _IDENTIFIER_RE.match(_sanitize_identifier(binder.name, "x")):
            issues.append(
                TranslationIssue(
                    code="binder_bad_name",
                    severity="warning",
                    message=f"Binder name '{binder.name}' is not a clean Lean identifier.",
                )
            )
        if not binder.type_expr:
            issues.append(
                TranslationIssue(
                    code="binder_missing_type",
                    severity="fatal",
                    message=f"Binder '{binder.name}' is missing its type.",
                )
            )
        binder_names.append(binder.name)

    duplicates = sorted({name for name in binder_names if binder_names.count(name) > 1})
    for name in duplicates:
        issues.append(
            TranslationIssue(
                code="duplicate_binder",
                severity="fatal",
                message=f"Binder '{name}' appears more than once.",
            )
        )

    step_ids = [step.step_id for step in plan.proof_steps]
    duplicate_steps = sorted({step_id for step_id in step_ids if step_ids.count(step_id) > 1})
    for step_id in duplicate_steps:
        issues.append(
            TranslationIssue(
                code="duplicate_step_id",
                severity="fatal",
                message=f"Proof step id '{step_id}' appears more than once.",
            )
        )

    seen_steps: set[str] = set()
    for step in plan.proof_steps:
        if not step.claim:
            issues.append(
                TranslationIssue(
                    code="missing_step_claim",
                    severity="warning",
                    message=f"Step '{step.step_id}' has no explicit claim; "
                            f"it will be rendered as a raw tactic block or sorry.",
                )
            )
        for dep in step.depends_on:
            if dep not in seen_steps and dep not in binder_names:
                issues.append(
                    TranslationIssue(
                        code="unknown_dependency",
                        severity="fatal",
                        message=f"Step '{step.step_id}' depends on unknown or future item '{dep}'.",
                    )
                )
        seen_steps.add(step.step_id)

    if plan.introduced_assumptions:
        issues.append(
            TranslationIssue(
                code="introduced_assumptions",
                severity="warning",
                message="Translator introduced assumptions that were not explicit in the source proof.",
            )
        )

    high_ambiguities = [a for a in plan.ambiguities if a.severity.lower() in {"high", "critical"}]
    if high_ambiguities:
        issues.append(
            TranslationIssue(
                code="high_ambiguity",
                severity="warning",
                message=f"High-severity ambiguities present: {len(high_ambiguities)}.",
            )
        )

    return issues


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Deterministic Lean rendering
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _indent_block(block: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    lines = block.splitlines() or [""]
    return "\n".join(prefix + line if line else prefix for line in lines)


def _json_comment(prefix: str, payload: Any) -> str:
    return f"-- {prefix}: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"


def render_plan_to_lean(plan: ForwardTranslationPlan) -> str:
    """Render a deterministic Lean skeleton from the normalized plan."""

    if not plan.parse_ok or not plan.conclusion:
        return ""

    lines: list[str] = []
    for imp in plan.imports or ["Mathlib"]:
        imp = imp.strip() or "Mathlib"
        lines.append(f"import {imp}")
    lines.append("")

    if plan.namespace:
        lines.append(f"namespace {_sanitize_identifier(plan.namespace, 'Audit')}")
        lines.append("")

    lines.append(f"theorem {plan.theorem_name}")
    for binder in plan.binders:
        name = _sanitize_identifier(binder.name, "x")
        if binder.role == "instance":
            lines.append(f"    [{name} : {binder.type_expr}]")
        elif binder.role == "hypothesis":
            lines.append(f"    ({name} : {binder.type_expr})")
        else:
            lines.append(f"    ({name} : {binder.type_expr})")
    lines.append(f"    : {plan.conclusion} := by")

    if not plan.proof_steps:
        final_proof = (plan.final_proof or "sorry").strip() or "sorry"
        lines.append("  -- FINAL_PROOF")
        lines.extend(_indent_block(final_proof, 2).splitlines())
    else:
        for idx, step in enumerate(plan.proof_steps, 1):
            lines.append(f"  -- SORRY_ID: {step.step_id}")
            if step.original_text:
                escaped = step.original_text.replace('"', "'")
                lines.append(f'  -- STEP {idx}: "{escaped}"')
            if step.reason:
                escaped_reason = step.reason.replace('"', "'")
                lines.append(f'  -- CLAIMED_REASON: "{escaped_reason}"')

            if step.claim:
                lines.append(f"  have {step.step_id} : {step.claim} := by")
                body = step.lean_code.strip()
                if step.status in {"sorry", "planned"} or not body:
                    lines.append("    sorry")
                else:
                    lines.extend(_indent_block(body, 4).splitlines())
            else:
                body = step.lean_code.strip()
                if body:
                    lines.extend(_indent_block(body, 2).splitlines())
                else:
                    lines.append("  sorry")
            lines.append("")

        final_proof = (plan.final_proof or "sorry").strip() or "sorry"
        lines.append("  -- FINAL_PROOF")
        lines.extend(_indent_block(final_proof, 2).splitlines())

    lines.append("")

    # Structured metadata as JSON comments for downstream parsing
    lines.append(_json_comment("AMBIGUITY_LEDGER", [a.__dict__ for a in plan.ambiguities]))
    lines.append(_json_comment("INTRODUCED_ASSUMPTIONS", plan.introduced_assumptions))

    if plan.namespace:
        lines.append("")
        lines.append("end " + _sanitize_identifier(plan.namespace, "Audit"))

    return "\n".join(lines).rstrip() + "\n"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AI interaction — plan generation and repair
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _build_forward_user_prompt(
    original_proof: str,
    theorem_name: str,
    mathlib_context: str = "",
    namespace: str = "",
    imports: Optional[list[str]] = None,
) -> str:
    imports_hint = imports or ["Mathlib"]
    namespace_hint = namespace or "(none)"
    imports_text = ", ".join(imports_hint)

    mathlib_section = ""
    if mathlib_context:
        mathlib_section = f"""
## Mathlib Reference Context

Use the EXACT Lean/Mathlib names from the reference below. Do NOT invent API names.

{mathlib_context}
"""

    return f"""Translate the following natural-language proof into a structured Lean translation plan.
Return ONLY a JSON object. You may wrap it in ```json ... ``` fences.

Requested theorem name: {theorem_name}
Requested namespace: {namespace_hint}
Preferred imports: {imports_text}
{mathlib_section}
Natural-language proof:
{original_proof}
"""


def _repair_translation_plan(
    client: AIClient,
    original_proof: str,
    current_plan: ForwardTranslationPlan,
    compile_errors: list[str],
    mathlib_context: str = "",
) -> ForwardTranslationPlan:
    """Ask the model to repair only syntactic/typing issues while preserving meaning.

    ⛔ CRITICAL: The repair must NEVER change the mathematical content.
    It may only fix Lean syntax, API names, and type errors.
    """

    system_prompt = (AGENTS_DIR / "forward_translator.md").read_text()
    scoped_client = _spawn_scoped_client(client, system_prompt)
    error_text = "\n".join(f"- {err}" for err in compile_errors) or "- unknown compiler error"

    mathlib_section = ""
    if mathlib_context:
        mathlib_section = f"""
## Mathlib Reference (use EXACT names from here):
{mathlib_context}
"""

    user_prompt = f"""Repair the following translation plan.

⛔ ABSOLUTE RULES FOR REPAIR:
1. Do NOT change the mathematical meaning of any step.
2. Do NOT add or remove proof steps.
3. Do NOT change the theorem statement or conclusion.
4. Only fix Lean syntax errors, incorrect API names, and type mismatches.
5. If you cannot fix a step safely, keep it as status="sorry".
6. Return ONLY JSON.

{mathlib_section}

Original natural-language proof:
{original_proof}

Current translation plan:
{json.dumps(current_plan.raw_payload or {}, ensure_ascii=False, indent=2)}

Compiler / elaboration feedback:
{error_text}
"""
    response = scoped_client.chat(user_prompt)
    return parse_forward_translation_plan(response.content, theorem_name=current_plan.theorem_name)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main entry point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def forward_translate(
    client: AIClient,
    original_proof: str,
    theorem_name: str = "audit_theorem",
    namespace: str = "",
    imports: Optional[list[str]] = None,
    compile_callback: Optional[CompileCallback] = None,
    max_repair_rounds: int = 1,
) -> ForwardTranslationResult:
    """Translate a natural-language proof into Lean via a structured plan.

    Pipeline:
      1. Detect relevant Mathlib domains from proof text
      2. Ask AI for a JSON translation plan (with Mathlib context injected)
      3. Parse and validate the plan
      4. Render Lean deterministically from the plan
      5. Optionally compile and repair (syntax only, never semantics)

    Args:
        client: AI client for model calls.
        original_proof: Natural language proof text.
        theorem_name: Desired Lean theorem name.
        namespace: Optional Lean namespace.
        imports: Preferred import list.
        compile_callback: If provided, enables compile-repair loop.
            Signature: (lean_code: str) -> (compiles: bool, errors: list[str])
        max_repair_rounds: Maximum compile-repair iterations.

    Returns:
        ForwardTranslationResult with plan, Lean code, and diagnostics.
    """

    # ── Step 1: Build Mathlib domain reference context ──
    mathlib_context = build_reference_context(original_proof)

    # ── Step 2: Ask AI for structured JSON plan ──
    system_prompt = (AGENTS_DIR / "forward_translator.md").read_text()
    scoped_client = _spawn_scoped_client(client, system_prompt)
    user_prompt = _build_forward_user_prompt(
        original_proof=original_proof,
        theorem_name=_sanitize_identifier(theorem_name, "audit_theorem"),
        mathlib_context=mathlib_context,
        namespace=namespace,
        imports=imports,
    )

    response = scoped_client.chat(user_prompt)
    plan = parse_forward_translation_plan(response.content, theorem_name=theorem_name)
    if namespace and not plan.namespace:
        plan.namespace = namespace
    if imports and not plan.imports:
        plan.imports = imports

    # ── Step 3: Validate plan ──
    issues = validate_forward_translation_plan(plan)

    # ── Step 4: Render Lean deterministically ──
    lean_code = render_plan_to_lean(plan)

    result = ForwardTranslationResult(
        plan=plan,
        lean_code=lean_code,
        issues=issues,
        requires_human_review=any(i.severity in {"warning", "fatal"} for i in issues),
        mathlib_context=mathlib_context,
    )

    # ── Step 5: Optional compile-repair loop ──
    if not compile_callback or not lean_code:
        return result

    current_plan = plan
    current_lean = lean_code
    for round_idx in range(max(0, max_repair_rounds) + 1):
        compiles, errors = compile_callback(current_lean)
        result.compiles = compiles
        result.compile_errors = errors
        if compiles:
            result.lean_code = current_lean
            return result
        if round_idx == max_repair_rounds:
            result.requires_human_review = True
            result.issues.append(
                TranslationIssue(
                    code="compile_failed",
                    severity="warning",
                    message="Rendered Lean did not compile after repair attempts.",
                )
            )
            result.lean_code = current_lean
            return result

        repaired_plan = _repair_translation_plan(
            client=client,
            original_proof=original_proof,
            current_plan=current_plan,
            compile_errors=errors,
            mathlib_context=mathlib_context,
        )
        repaired_issues = validate_forward_translation_plan(repaired_plan)
        current_plan = repaired_plan
        current_lean = render_plan_to_lean(repaired_plan)
        result.plan = repaired_plan
        result.lean_code = current_lean
        result.issues = repaired_issues
        result.requires_human_review = any(i.severity in {"warning", "fatal"} for i in repaired_issues)
        result.repaired_rounds = round_idx + 1

    return result
