import importlib.util
import json
from pathlib import Path
import threading

MODULE_PATH = Path(__file__).resolve().parents[1] / "__init__.py"
SPEC = importlib.util.spec_from_file_location("hermes_decode_plugin", MODULE_PATH)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


def test_single_sample_speed():
    result = plugin.DecodeAggregator().record("s", {"output_tokens": 300}, 3)
    assert result == {"tok_per_sec": 100, "valid_samples": 1, "measured": True}


def test_ratio_of_sums_not_mean_of_rates():
    state = plugin.DecodeAggregator()
    state.record("s", {"output_tokens": 120}, 2)
    result = state.record("s", {"output_tokens": 180}, 4)
    assert result["tok_per_sec"] == 50
    assert result["tok_per_sec"] != (60 + 45) / 2


def test_speed_is_rounded_half_up():
    state = plugin.DecodeAggregator()
    assert state.record("s", {"output_tokens": 3}, 2)["tok_per_sec"] == 2
    assert state.record("s2", {"output_tokens": 5}, 2)["tok_per_sec"] == 3


def test_unmeasured_call_keeps_prior_cumulative_speed():
    state = plugin.DecodeAggregator()
    assert state.record("s", None, None) == {
        "tok_per_sec": None, "valid_samples": 0, "measured": False
    }
    state.record("s", {"output_tokens": 300}, 3)
    assert state.record("s", None, None) == {
        "tok_per_sec": 100, "valid_samples": 1, "measured": False
    }
    assert state.totals_for("s") == (300, 3, 1)


def test_more_than_ten_calls_never_evict_older_measurements():
    state = plugin.DecodeAggregator()
    state.record("s", {"output_tokens": 10000}, 1)
    for _ in range(10):
        result = state.record("s", {"output_tokens": 10}, 1)
    assert result["valid_samples"] == 11
    assert result["tok_per_sec"] == 918
    assert state.totals_for("s") == (10100, 11, 11)


def test_sessions_never_share_totals():
    state = plugin.DecodeAggregator()
    first = state.record("s-1", {"output_tokens": 200}, 2)
    second = state.record("s-2", {"output_tokens": 200}, 5)
    assert first["tok_per_sec"] == 100
    assert second["tok_per_sec"] == 40
    assert state.totals_for("s-1") == (200, 2, 1)


def test_model_and_endpoint_changes_keep_session_cumulative(monkeypatch):
    results = emitted_hooks(monkeypatch, [
        {"session_id": "s", "provider": "p1", "model": "m1",
         "base_url": "https://example.invalid/a",
         "usage": {"output_tokens": 200}, "decode_seconds": 2},
        {"session_id": "s", "provider": "p2", "model": "m2",
         "base_url": "https://example.invalid/b",
         "usage": {"output_tokens": 200}, "decode_seconds": 4},
    ])
    assert [p["valid_samples"] for p in results] == [1, 2]
    assert [p["tok_per_sec"] for p in results] == [100, 67]


def emitted_hooks(monkeypatch, calls):
    emitted = []
    monkeypatch.setattr(plugin, "_broadcast", lambda event, payload: emitted.append((event, payload)))
    monkeypatch.setattr(plugin, "_AGGREGATOR", plugin.DecodeAggregator())
    for call in calls:
        plugin.post_api_request(**call)
    assert all(event == "sample" for event, _ in emitted)
    return [payload for _, payload in emitted]


def test_hook_sessions_share_nothing(monkeypatch):
    results = emitted_hooks(monkeypatch, [
        {"session_id": "s1", "provider": "p", "model": "m",
         "usage": {"output_tokens": 200}, "decode_seconds": 2},
        {"session_id": "s2", "provider": "p", "model": "m",
         "usage": {"output_tokens": 200}, "decode_seconds": 5},
    ])
    assert [p["tok_per_sec"] for p in results] == [100, 40]


def test_hook_emits_only_contract_fields(monkeypatch):
    result = emitted_hooks(monkeypatch, [
        {"session_id": "s", "provider": "p", "model": "m",
         "usage": {"output_tokens": 300}, "decode_seconds": 3,
         "response": "private", "base_url": "https://private.invalid"},
    ])[-1]
    assert result == {"schema_version": 3, "session_id": "s", "provider": "p",
                      "model": "m", "tok_per_sec": 100,
                      "measured": True, "valid_samples": 1}
    assert "private.invalid" not in json.dumps(result)


def test_hook_missing_identity_is_silent(monkeypatch):
    assert emitted_hooks(monkeypatch, [
        {"provider": "p", "model": "m"},
        {"session_id": "s", "model": "m"},
        {"session_id": "s", "provider": "", "model": "m"},
    ]) == []


def test_invalid_metrics_are_not_accumulated():
    state = plugin.DecodeAggregator()
    cases = [
        ({"output_tokens": 12}, float("nan")),
        ({"output_tokens": 12}, float("inf")),
        ({"output_tokens": 12}, 0),
        (None, 1),
        ({}, 1),
        ({"output_tokens": 0}, 1),
        ({"output_tokens": float("nan")}, 1),
        ({"output_tokens": float("inf")}, 1),
    ]
    for usage, seconds in cases:
        result = state.record("s", usage, seconds)
        assert result == {"tok_per_sec": None, "valid_samples": 0, "measured": False}


def test_invalid_latest_does_not_clear_existing_total():
    state = plugin.DecodeAggregator()
    state.record("s", {"output_tokens": 100}, 1)
    assert state.record("s", {"output_tokens": 100}, 0) == {
        "tok_per_sec": 100, "valid_samples": 1, "measured": False
    }


def test_overflow_cannot_poison_cumulative_totals():
    state = plugin.DecodeAggregator()
    state.record("s", {"output_tokens": 100}, 1)
    result = state.record("s", {"output_tokens": 1e308}, 1e308)
    assert result["measured"] is True
    result = state.record("s", {"output_tokens": 1e308}, 1e308)
    assert result["measured"] is False
    assert result["valid_samples"] == 2


def test_session_history_is_not_lru_evicted():
    state = plugin.DecodeAggregator()
    for index in range(100):
        state.record(f"s-{index}", {"output_tokens": 10}, 1)
    assert state.record("s-0", {"output_tokens": 10}, 1)["valid_samples"] == 2


def test_concurrent_calls_keep_cumulative_totals():
    state = plugin.DecodeAggregator()
    barrier = threading.Barrier(7)
    failures = []

    def hammer(index):
        barrier.wait()
        try:
            for _ in range(40):
                state.record("shared" if index < 2 else f"s-{index}",
                             {"output_tokens": 10}, 1)
        except Exception as error:
            failures.append(error)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert failures == []
    assert state.totals_for("shared") == (800, 80, 80)
    for i in range(2, 6):
        assert state.totals_for(f"s-{i}") == (400, 40, 40)


def test_plugin_reload_resets_in_memory_state():
    previous = plugin.DecodeAggregator()
    previous.record("s", {"output_tokens": 100}, 1)
    spec = importlib.util.spec_from_file_location("hermes_decode_plugin", MODULE_PATH)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh._AGGREGATOR.totals_for("s") == (0.0, 0.0, 0)
    assert previous.totals_for("s") == (100, 1, 1)


def test_register_only_uses_post_api_request():
    class Context:
        registrations = []
        def register_hook(self, name, callback):
            self.registrations.append((name, callback))
    ctx = Context()
    plugin.register(ctx)
    assert ctx.registrations == [("post_api_request", plugin.post_api_request)]
