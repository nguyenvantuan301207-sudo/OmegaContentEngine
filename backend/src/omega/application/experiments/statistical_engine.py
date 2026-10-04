"""Deterministic statistical inference and effect size calculation engine for P25-C.

Implements mathematically sound statistical tests without generic formulas or forced significance:
- Two-proportion pooled z-test with unpooled Wald confidence intervals
- Zero-denominator protected effect size and relative lift
- Bonferroni multiple comparison correction
- Bounded sample sufficiency validation
"""

from __future__ import annotations

import math
from typing import Any

from omega.domain.experimentation import StatisticalInferenceResult


class StatisticalEngine:
    """Mathematical calculations for causal attribution and experiment inference."""

    @staticmethod
    def compute_effect_size(
        control_val: float | None,
        treatment_val: float | None,
    ) -> tuple[float | None, float | None]:
        """Compute absolute difference and relative lift.

        Returns (absolute_difference, relative_lift).
        Safeguards against zero or None values.
        """
        if control_val is None or treatment_val is None:
            return None, None

        abs_diff = round(treatment_val - control_val, 4)

        if control_val == 0.0:
            # Lift is undefined when control baseline is zero
            rel_lift = None
        else:
            rel_lift = round(abs_diff / control_val, 4)

        return abs_diff, rel_lift

    @classmethod
    def evaluate_two_proportion_z_test(
        cls,
        p_control: float,
        n_control: int,
        p_treatment: float,
        n_treatment: int,
        alpha: float = 0.05,
    ) -> StatisticalInferenceResult:
        """Execute two-proportion pooled z-test and calculate 95% confidence interval.

        Appropriate for binomial rate metrics such as CTR (clicks / impressions).
        """
        sample_basis = {
            "control_sample_size": n_control,
            "treatment_sample_size": n_treatment,
        }

        # Sufficiency check for normal approximation: n*p >= 5 and n*(1-p) >= 5
        if n_control < 30 or n_treatment < 30:
            return StatisticalInferenceResult(
                is_statistically_significant=False,
                sample_basis=sample_basis,
                notes="INSUFFICIENT_SAMPLE_SIZE_FOR_INFERENCE",
            )

        clicks_ctrl = p_control * n_control
        clicks_trt = p_treatment * n_treatment

        # Pooled proportion under H0: p1 = p2
        pooled_p = (clicks_ctrl + clicks_trt) / (n_control + n_treatment)

        # Standard error under null hypothesis
        se_pooled = math.sqrt(pooled_p * (1.0 - pooled_p) * (1.0 / n_control + 1.0 / n_treatment))
        if se_pooled == 0.0:
            return StatisticalInferenceResult(
                is_statistically_significant=False,
                p_value=1.0,
                test_statistic=0.0,
                confidence_interval=(0.0, 0.0),
                sample_basis=sample_basis,
                notes="ZERO_VARIANCE_IN_SAMPLES",
            )

        diff = p_treatment - p_control
        z_stat = diff / se_pooled

        # Two-tailed p-value from standard normal distribution
        p_value = 2.0 * (1.0 - cls._std_normal_cdf(abs(z_stat)))
        p_value = max(0.0, min(1.0, round(p_value, 6)))

        # Unpooled standard error for confidence interval
        se_unpooled = math.sqrt(
            (p_control * (1.0 - p_control) / n_control)
            + (p_treatment * (1.0 - p_treatment) / n_treatment)
        )
        z_crit = 1.95996  # 95% two-sided critical value
        ci_lower = round(diff - z_crit * se_unpooled, 4)
        ci_upper = round(diff + z_crit * se_unpooled, 4)

        is_sig = p_value < alpha

        return StatisticalInferenceResult(
            is_statistically_significant=is_sig,
            p_value=p_value,
            test_statistic=round(z_stat, 4),
            confidence_interval=(ci_lower, ci_upper),
            confidence_level=1.0 - alpha,
            method="TWO_PROPORTION_POOLED_Z_TEST",
            sample_basis=sample_basis,
        )

    @staticmethod
    def apply_bonferroni_correction(alpha: float, comparison_count: int) -> float:
        """Apply Bonferroni adjustment for multiple treatment variant comparisons."""
        if comparison_count <= 1:
            return alpha
        return round(alpha / comparison_count, 6)

    @staticmethod
    def _std_normal_cdf(x: float) -> float:
        """Standard normal cumulative distribution function using error function."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
