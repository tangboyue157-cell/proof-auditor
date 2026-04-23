"""Tests for core.mathlib_reference — domain detection and reference assembly."""

import pytest

from core.mathlib_reference import (
    detect_domains,
    load_domain_reference,
    match_patterns,
    build_reference_context,
)


class TestDetectDomains:
    """Test keyword-based domain detection."""

    def test_elementary_number_theory(self):
        text = "Let a and b be two odd integers. We will prove that a + b is even."
        domains = detect_domains(text)
        assert "elementary_number_theory" in domains

    def test_probability(self):
        text = "By the Central Limit Theorem, the distribution of the sample mean converges to a Gaussian."
        domains = detect_domains(text)
        assert "probability" in domains

    def test_measure_theory(self):
        text = "Since f is measurable and the measure μ is σ-finite, by Fubini's theorem..."
        domains = detect_domains(text)
        assert "measure_theory" in domains

    def test_real_analysis(self):
        text = "The function f is continuous on [a,b], hence by the mean value theorem..."
        domains = detect_domains(text)
        assert "real_analysis" in domains

    def test_linear_algebra(self):
        text = "The matrix A has eigenvalue λ and the determinant is nonzero."
        domains = detect_domains(text)
        assert "linear_algebra" in domains

    def test_multiple_domains(self):
        """Probability proofs often need measure theory."""
        text = (
            "Let X be a random variable with finite variance. "
            "Since X is measurable and integrable, E[X] is well-defined."
        )
        domains = detect_domains(text)
        assert len(domains) >= 2
        assert "probability" in domains
        # measure_theory should also be detected due to "measurable" and "integrable"
        assert "measure_theory" in domains

    def test_empty_text(self):
        domains = detect_domains("")
        assert domains == []

    def test_no_matching_domain(self):
        text = "This is about topology and homology groups."
        domains = detect_domains(text)
        # Should match nothing or very weakly
        # "topology" isn't a trigger; "groups" isn't either
        # The text shouldn't match any domain strongly
        assert len(domains) <= 1  # Maybe weak match on "uniform" in real_analysis

    def test_ordering_by_relevance(self):
        """Most relevant domain should be first."""
        text = "odd even parity divisible prime integer"
        domains = detect_domains(text)
        assert domains[0] == "elementary_number_theory"


class TestLoadDomainReference:
    """Test loading domain reference files."""

    def test_load_existing_domain(self):
        ref = load_domain_reference("probability")
        assert ref is not None
        assert "ProbabilityTheory" in ref
        assert "Translation trap" in ref

    def test_load_nonexistent_domain(self):
        ref = load_domain_reference("quantum_computing")
        assert ref is None

    def test_all_domains_loadable(self):
        """Every domain in DOMAIN_TRIGGERS should have a reference file."""
        from core.mathlib_reference import DOMAIN_TRIGGERS
        for domain in DOMAIN_TRIGGERS:
            ref = load_domain_reference(domain)
            assert ref is not None, f"Missing reference file for domain: {domain}"


class TestMatchPatterns:
    """Test translation pattern matching."""

    def test_odd_even_pattern(self):
        text = "Let a be an odd integer."
        matches = match_patterns(text)
        assert len(matches) >= 1
        assert any("Odd" in m.get("guidance", "") for m in matches)

    def test_limit_pattern(self):
        text = "The sequence converges to L as n tends to infinity."
        matches = match_patterns(text)
        assert len(matches) >= 1
        assert any("filter" in m.get("guidance", "").lower() for m in matches)

    def test_expectation_pattern(self):
        text = "The expectation E[X] equals the integral of X."
        matches = match_patterns(text)
        assert len(matches) >= 1
        assert any("Bochner" in m.get("guidance", "") or "integral" in m.get("guidance", "")
                    for m in matches)

    def test_severity_ordering(self):
        """High severity patterns should come first."""
        text = "odd limit continuous measurable expectation"
        matches = match_patterns(text)
        severities = [m.get("severity", "low") for m in matches]
        # All "high" should come before "medium", which before "low"
        seen_medium = False
        seen_low = False
        for s in severities:
            if s == "medium":
                seen_medium = True
            if s == "low":
                seen_low = True
            if s == "high" and (seen_medium or seen_low):
                pytest.fail(f"High severity pattern appeared after lower: {severities}")

    def test_no_patterns_for_irrelevant_text(self):
        text = "This is about topology."
        matches = match_patterns(text)
        # Should have zero or very few matches
        assert len(matches) <= 1


class TestBuildReferenceContext:
    """Test the main API that builds the complete reference context."""

    def test_basic_output_structure(self):
        text = "Let a and b be two odd integers."
        ctx = build_reference_context(text)
        assert "## Lean 4 / Mathlib Quick Reference" in ctx
        assert "EXACT definition names" in ctx

    def test_includes_domain_reference(self):
        text = "Let a and b be two odd integers."
        ctx = build_reference_context(text)
        assert "Odd" in ctx or "Even" in ctx

    def test_includes_translation_warnings(self):
        text = "Let a be an odd integer. The expectation of X converges in distribution."
        ctx = build_reference_context(text)
        assert "Translation Warnings" in ctx or "⚠️" in ctx

    def test_max_domains_respected(self):
        """Should limit number of domain references."""
        text = (
            "odd even limit continuous measurable random variable "
            "matrix eigenvalue variance integral"
        )
        ctx = build_reference_context(text, max_domains=2)
        # Count domain sections
        domain_count = ctx.count("### Domain:")
        assert domain_count <= 2

    def test_empty_for_no_domains(self):
        ctx = build_reference_context("")
        assert ctx == ""

    def test_probability_proof_context(self):
        """Phase 1 benchmark proof should get probability + measure theory refs."""
        text = """
        Let X₁, X₂, ..., Xₙ be i.i.d. random variables with distribution
        Uniform[0,1]. Define Pₙ = X₁ · X₂ · ... · Xₙ. We want to show that
        Pₙ^{1/√n} converges in distribution to some limit using the CLT and
        Delta Method.
        """
        ctx = build_reference_context(text)
        # Should detect probability domain
        assert "ProbabilityTheory" in ctx
        # Should include CLT warning
        assert "CLT" in ctx or "central limit" in ctx.lower()
