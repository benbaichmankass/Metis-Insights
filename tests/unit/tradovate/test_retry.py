"""Exponential backoff bounds + monotonic growth."""
from src.units.accounts.tradovate.retry import exponential_backoff


def test_first_attempt_close_to_base():
    # With jitter ±25%, attempt=1 should land in [0.375, 0.625]
    samples = [exponential_backoff(1) for _ in range(50)]
    assert all(0.3 < s < 0.7 for s in samples)


def test_caps_after_growth():
    samples = [exponential_backoff(20, cap_s=5.0) for _ in range(30)]
    assert all(s <= 5.0 * 1.25 + 1e-9 for s in samples)


def test_attempt_zero_does_not_crash():
    # Clamped to 1 internally.
    s = exponential_backoff(0)
    assert s > 0


def test_no_jitter_is_deterministic():
    assert exponential_backoff(3, base_s=0.5, cap_s=10.0, jitter=0.0) == 2.0
