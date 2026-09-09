from datetime import datetime, timezone

from capitalguard.application.services.adaptive_historical_replay import (
    AdaptiveHistoricalReplayPlanner,
    LifecycleState,
)


UTC = timezone.utc


def test_day_zero_starts_at_signal_time_and_does_not_cross_day_boundary():
    planner = AdaptiveHistoricalReplayPlanner()
    window = planner.minute_window_for_day(
        day=datetime(2026, 1, 10, 0, tzinfo=UTC),
        signal_source_time=datetime(2026, 1, 10, 22, 30, tzinfo=UTC),
        now=datetime(2026, 1, 11, 12, tzinfo=UTC),
    )
    assert window is not None
    assert window.start == datetime(2026, 1, 10, 22, 30, tzinfo=UTC)
    assert window.end == datetime(2026, 1, 11, 0, tzinfo=UTC)
    assert window.limit == 90


def test_late_day_never_fetches_a_full_1000_plus_440_page_pair():
    planner = AdaptiveHistoricalReplayPlanner()
    window = planner.minute_window_for_day(
        day=datetime(2026, 1, 10, tzinfo=UTC),
        signal_source_time=datetime(2026, 1, 10, 22, 30, tzinfo=UTC),
        now=datetime(2026, 1, 10, 23, 59, tzinfo=UTC),
    )
    assert window is not None
    pages = list(planner.minute_page_windows(window))
    assert [page.limit for page in pages] == [89]
    assert all(page.end <= datetime(2026, 1, 11, tzinfo=UTC) for page in pages)


def test_full_day_is_two_pages_max_1000_and_440():
    planner = AdaptiveHistoricalReplayPlanner()
    window = planner.minute_window_for_day(
        day=datetime(2026, 1, 10, tzinfo=UTC),
        signal_source_time=datetime(2026, 1, 9, tzinfo=UTC),
        now=datetime(2026, 1, 11, tzinfo=UTC),
    )
    assert window is not None
    pages = list(planner.minute_page_windows(window))
    assert [page.limit for page in pages] == [1000, 440]
    assert planner.page_count(window) == 2
    assert pages[-1].end == datetime(2026, 1, 11, tzinfo=UTC)


def test_lifecycle_state_persists_remaining_targets_and_fixed_stop():
    # Verification gate: historical lifecycle must not silently trail the original stop.
    state = LifecycleState(
        activated=True,
        hit_target_indices=frozenset({1}),
        remaining_target_indices=frozenset({2, 3}),
        current_stop=100,
        lifecycle_state="ACTIVE_POSITION",
    )
    state = AdaptiveHistoricalReplayPlanner.lifecycle_after_event(
        state,
        event_type="TP2",
        target_index=2,
        stop=100,
        timestamp=datetime(2026, 1, 11, tzinfo=UTC),
    )
    assert state.hit_target_indices == frozenset({1, 2})
    assert state.remaining_target_indices == frozenset({3})
    assert state.current_stop == 100
    assert state.lifecycle_state == "ACTIVE_POSITION"


def test_terminal_lifecycle_is_terminal():
    state = LifecycleState(activated=True, remaining_target_indices=frozenset({2}))
    state = AdaptiveHistoricalReplayPlanner.lifecycle_after_event(
        state, event_type="SL", timestamp=datetime(2026, 1, 12, tzinfo=UTC)
    )
    assert state.lifecycle_state == "CLOSED_STOP"
    assert AdaptiveHistoricalReplayPlanner.terminal(state)
