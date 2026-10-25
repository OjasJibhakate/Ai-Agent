"""The circuit breaker: a crashing tool is paused, then tried once, then trusted again."""

from ragnarok.recovery.breaker import CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_three_failures_in_a_row_open_it_for_the_cooldown():
    clock = Clock()
    b = CircuitBreaker(clock=clock)
    for _ in range(2):
        b.failure("mail")
    assert b.blocked_for("mail") == 0 and not b.is_open("mail")  # two is not enough
    b.failure("mail")
    assert b.is_open("mail") and 59 < b.blocked_for("mail") <= 60
    assert b.blocked_for("calendar") == 0  # other tools are untouched


def test_a_success_in_between_resets_the_count():
    b = CircuitBreaker(clock=Clock())
    b.failure("mail")
    b.failure("mail")
    b.success("mail")
    b.failure("mail")
    assert not b.is_open("mail")


def test_after_the_pause_one_trial_is_allowed_and_a_success_closes_it():
    clock = Clock()
    b = CircuitBreaker(clock=clock)
    for _ in range(3):
        b.failure("mail")
    clock.now += 61
    assert b.blocked_for("mail") == 0  # the trial
    assert b.blocked_for("mail") > 0  # nobody else gets through while it runs
    b.success("mail")
    assert not b.is_open("mail") and b.blocked_for("mail") == 0


def test_a_failed_trial_starts_the_pause_again():
    clock = Clock()
    b = CircuitBreaker(clock=clock)
    for _ in range(3):
        b.failure("mail")
    clock.now += 61
    assert b.blocked_for("mail") == 0
    b.failure("mail")
    assert b.is_open("mail") and b.blocked_for("mail") > 59
