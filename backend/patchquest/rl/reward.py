"""Decomposed reward.

Every step returns a :class:`RewardBreakdown`; the scalar reward is ``breakdown.total`` (the sum of components).
Components and their exact semantics (weights live in the frozen :class:`RewardConfig`):

* ``oracle``        ``+oracle_success`` once, at episode end (``finish`` or step-cap truncation), when the hidden
                    oracle command passes on the final workspace. Otherwise 0. Never available mid-episode.
* ``test_fraction`` ``test_fraction * (f_now - f_prev)`` on every ``run_tests`` step, where ``f`` is the fraction of
                    visible tests passing. ``f_prev`` starts as the fraction measured on the pristine workspace
                    (taken lazily, just before the first mutation), so the component telescopes: summed over an
                    episode it equals ``test_fraction * (f_last - f_baseline)``.
* ``patch_size``    ``-min(patch_size_cap, patch_size_per_line * changed_lines)`` once, at episode end, where
                    ``changed_lines`` counts added + removed lines of the workspace diff. Charged once so editing
                    and re-editing the same lines is not double penalised.
* ``step_cost``     ``-step_cost`` on every step, including invalid ones and ``finish``.
* ``safety``        ``-safety_violation`` per policy-blocked command (a ``run_tests`` whose command the command
                    policy does not auto-approve), plus once at episode end if a ``forbid_changes`` file differs
                    from its initial content.
* ``invalid``       ``-invalid_action`` on every malformed/unknown action (it still costs ``step_cost``). A
                    well-formed action that fails to apply (ambiguous edit, missing file) is NOT invalid.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RewardConfig:
    oracle_success: float = 1.0
    test_fraction: float = 0.2
    patch_size_per_line: float = 0.002
    patch_size_cap: float = 0.2
    step_cost: float = 0.01
    safety_violation: float = 0.5
    invalid_action: float = 0.05


@dataclass(frozen=True)
class RewardBreakdown:
    oracle: float = 0.0
    test_fraction: float = 0.0
    patch_size: float = 0.0
    step_cost: float = 0.0
    safety: float = 0.0
    invalid: float = 0.0

    @property
    def total(self) -> float:
        return self.oracle + self.test_fraction + self.patch_size + self.step_cost + self.safety + self.invalid

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total": self.total}
