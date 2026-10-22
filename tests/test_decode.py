import importlib.util
import json
from pathlib import Path
import threading

MODULE_PATH = Path(__file__).resolve().parents[1] / "__init__.py"
SPEC = importlib.util.spec_from_file_location("hermes_decode_plugin", MODULE_PATH)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


def test_single_sample_speed():
    state = plugin.DecodeAggregator()
    result = state.record("session", "model", None, {"output_tokens": 300}, 3)
    assert result == {"tok_per_sec": 100, "valid_samples": 1, "measured": True}


def test_ratio_of_sums_not_mean_of_rates():
    state = plugin.DecodeAggregator()
    state.record("s", "m", None, {"output_tokens": 120}, 2)
    result = state.record("s", "m", None, {"output_tokens": 180}, 4)
    assert result["tok_per_sec"] == 50
    assert result["tok_per_sec"] != (60 + 45) / 2


def test_unmeasured_latest_call_reports_no_current_speed():
    state = plugin.DecodeAggregator()
    state.record("s", "m", None, {"output_tokens": 300}, 3)
    result = state.record("s", "m", None, None, None)
    # The latest call measured nothing, so the readout is empty even though the
    # window still holds the earlier measured sample.
    assert result == {"tok_per_sec": None, "valid_samples": 1, "measured": False}
    assert len(state.history_for("s", None)) == 2


def test_history_is_reused_at_the_next_measured_call():
    state = plugin.DecodeAggregator()
    state.record("s", "m", None, {"output_tokens": 300}, 3)
    state.record("s", "m", None, None, None)
    result = state.record("s", "m", None, {"output_tokens": 300}, 3)
    # The unmeasured call stayed in the window, so the next measured call
    # aggregates the history again.
    assert result == {"tok_per_sec": 100, "valid_samples": 2, "measured": True}


def test_eleventh_call_evicts_oldest_sample():
    state = plugin.DecodeAggregator()
    state.record("s", "m", None, {"output_tokens": 10000}, 1)
    for _ in range(10):
        result = state.record("s", "m", None, {"output_tokens": 10}, 1)
    assert len(state.history_for("s", None)) == 10
    assert result["tok_per_sec"] == 10
    assert result["valid_samples"] == 10


def test_sessions_never_share_a_window():
    state = plugin.DecodeAggregator()
    first = state.record("s-1", "m", None, {"output_tokens": 200}, 2)
    second = state.record("s-2", "m", None, {"output_tokens": 200}, 5)
    assert (first["valid_samples"], second["valid_samples"]) == (1, 1)
    assert (first["tok_per_sec"], second["tok_per_sec"]) == (100, 40)
    assert state.retained_window_count == 2


def test_connection_endpoints_stay_isolated_inside_one_session():
    state = plugin.DecodeAggregator()
    via_a = state.record("s", "m", "c-a", {"output_tokens": 200}, 2)
    via_b = state.record("s", "m", "c-b", {"output_tokens": 200}, 4)
    assert (via_a["tok_per_sec"], via_b["tok_per_sec"]) == (100, 50)
    assert (via_a["valid_samples"], via_b["valid_samples"]) == (1, 1)
    assert len(state.history_for("s", "c-a")) == 1
    assert len(state.history_for("s", "c-b")) == 1


def test_model_change_resets_the_session_history():
    state = plugin.DecodeAggregator()
    first = state.record("s", "m1", None, {"output_tokens": 200}, 2)
    second = state.record("s", "m2", None, {"output_tokens": 200}, 5)
    third = state.record("s", "m1", None, {"output_tokens": 200}, 4)
    assert [result["valid_samples"] for result in (first, second, third)] == [1, 1, 1]
    # The old model's measurement is gone from this session, not borrowed back.
    assert len(state.history_for("s", None)) == 1
    assert state.history_for("s", None)[0] == (200.0, 4.0)


def test_hook_sessions_share_nothing(monkeypatch):
    emitted = []
    monkeypatch.setattr(plugin, "_broadcast", lambda event, payload: emitted.append(payload))
    plugin._AGGREGATOR = plugin.DecodeAggregator()
    plugin.post_api_request(session_id="first", provider="p", model="m",
                            usage={"output_tokens": 200}, decode_seconds=2)
    plugin.post_api_request(session_id="second", provider="p", model="m",
                            usage={"output_tokens": 200}, decode_seconds=5)
    assert [payload["valid_samples"] for payload in emitted] == [1, 1]
    assert [payload["tok_per_sec"] for payload in emitted] == [100, 40]
    assert [emitted[-1]["session_id"], emitted[-2]["session_id"]] == ["second", "first"]


def test_another_model_never_borrows_the_window(monkeypatch):
    emitted = []
    monkeypatch.setattr(plugin, "_broadcast", lambda event, payload: emitted.append(payload))
    plugin._AGGREGATOR = plugin.DecodeAggregator()
    plugin.post_api_request(session_id="s", provider="p", model="m1",
                            usage={"output_tokens": 200}, decode_seconds=2)
    plugin.post_api_request(session_id="s", provider="p", model="m2",
                            usage={"output_tokens": 200}, decode_seconds=2)
    assert [payload["valid_samples"] for payload in emitted] == [1, 1]


def test_endpoint_key_is_normalized_and_stays_in_process():
    # The endpoint key only ever indexes this process's own windows; the
    # desktop half never sees it, so no digest is needed.
    assert plugin._endpoint_key("https://private.invalid") == "https://private.invalid"
    assert plugin._endpoint_key("https://private.invalid/") == "https://private.invalid"
    assert plugin._endpoint_key(" https://Private.Invalid ") == "https://private.invalid"
    assert plugin._endpoint_key("https://other.invalid") != plugin._endpoint_key("https://private.invalid")
    assert plugin._endpoint_key(None) is None
    assert plugin._endpoint_key("") is None
    assert plugin._endpoint_key("   / ") is None


def test_payload_carries_no_endpoint_or_generation_identity(monkeypatch):
    emitted = []
    monkeypatch.setattr(plugin, "_broadcast", lambda event, payload: emitted.append(payload))
    plugin._AGGREGATOR = plugin.DecodeAggregator()
    plugin.post_api_request(session_id="s", provider="p", model="m",
                            usage={"output_tokens": 200}, decode_seconds=2,
                            base_url="https://private.invalid")
    payload = emitted[-1]
    # The endpoint never reaches the desktop half: no URL, no digest of it, and
    # no process-generation id either.
    assert "conn" not in payload and "gen" not in payload
    assert "private.invalid" not in json.dumps(payload)


def test_invalid_metrics_and_usage_are_unmeasured():
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
        result = state.record("p", "m", None, usage, seconds)
        assert result["tok_per_sec"] is None
        assert result["valid_samples"] == 0
        assert result["measured"] is False


def test_hook_emits_minimal_payload(monkeypatch):
    emitted = []
    monkeypatch.setattr(plugin, "_broadcast", lambda event, payload: emitted.append((event, payload)))
    plugin._AGGREGATOR = plugin.DecodeAggregator()
    plugin.post_api_request(session_id="session", provider="provider", model="model",
                            usage={"output_tokens": 300}, decode_seconds=3,
                            response="private", base_url="https://private.invalid")
    event, payload = emitted[-1]
    assert event == "sample"
    assert payload == {"schema_version": 2, "session_id": "session", "provider": "provider",
                       "model": "model", "tok_per_sec": 100, "measured": True,
                       "valid_samples": 1}


def test_hook_without_identity_stays_silent(monkeypatch):
    emitted = []
    monkeypatch.setattr(plugin, "_broadcast", lambda event, payload: emitted.append(payload))
    plugin._AGGREGATOR = plugin.DecodeAggregator()
    for kwargs in [{"provider": "p", "model": "m"}, {"session_id": "s", "model": "m"},
                   {"session_id": "s", "provider": "", "model": "m"}]:
        plugin.post_api_request(**kwargs)
    assert emitted == []


def test_windows_are_bounded_to_eight_session_endpoint_pairs():
    state = plugin.DecodeAggregator()
    for index in range(9):
        state.record(f"s-{index}", "m", None, {"output_tokens": 10}, 1)
    assert state.retained_window_count == 8
    assert state.history_for("s-0", None) == ()
    assert len(state.history_for("s-8", None)) == 1
    # The evicted session holds nothing — not a window and not a remembered model:
    # its next call starts a fresh window instead of resuming the evicted one.
    assert state.record("s-0", "m", None, {"output_tokens": 10}, 1)["valid_samples"] == 1
    assert state.retained_window_count == 8


def test_register_installs_only_post_api_request_hook():
    class Context:
        registrations = []

        def register_hook(self, name, callback):
            self.registrations.append((name, callback))

    context = Context()
    plugin.register(context)
    assert context.registrations == [("post_api_request", plugin.post_api_request)]


def test_concurrent_model_calls_keep_windows_consistent():
    state = plugin.DecodeAggregator()
    keys = [(f"s-{index}", f"c-{index}") for index in range(6)]
    calls_per_key = 20
    # A worker thread's failure is invisible to pytest, so workers report to the
    # parent instead of asserting on their own stack.
    failures = []

    def hammer(index):
        session, endpoint = keys[index]
        barrier.wait()
        try:
            for call_index in range(calls_per_key):
                result = state.record(session, f"m-{index}", endpoint, {"output_tokens": 10}, 1)
                assert result["valid_samples"] == min(call_index + 1, plugin.WINDOW_SIZE)
        except Exception as error:
            failures.append(error)

    def shared_hammer():
        barrier.wait()
        try:
            for _ in range(calls_per_key):
                state.record("shared", "m-shared", None, {"output_tokens": 10}, 1)
        except Exception as error:
            failures.append(error)

    barrier = threading.Barrier(len(keys) + 2)
    threads = [threading.Thread(target=hammer, args=(index,)) for index in range(len(keys))]
    threads.append(threading.Thread(target=shared_hammer))
    threads.append(threading.Thread(target=shared_hammer))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == [], f"concurrent record failed: {failures!r}"
    # Concurrent calls never lost an append or blurred a window: every hammered
    # window holds exactly its last ten samples, and the LRU cap stayed honest.
    assert state.retained_window_count == 7
    for session, endpoint in keys:
        window = state.history_for(session, endpoint)
        assert len(window) == plugin.WINDOW_SIZE
        assert all(sample is not None for sample in window)
    assert len(state.history_for("shared", None)) == plugin.WINDOW_SIZE


def test_plugin_reload_resets_the_aggregator_state():
    previous_aggregator = plugin._AGGREGATOR
    previous_aggregator.record("s", "m", None, {"output_tokens": 100}, 1)

    fresh_spec = importlib.util.spec_from_file_location("hermes_decode_plugin", MODULE_PATH)
    fresh = importlib.util.module_from_spec(fresh_spec)
    fresh_spec.loader.exec_module(fresh)

    assert fresh._AGGREGATOR is not previous_aggregator
    emitted = []
    fresh._broadcast = lambda event, payload: emitted.append(payload)
    fresh.post_api_request(session_id="s", provider="p", model="m",
                           usage={"output_tokens": 200}, decode_seconds=2)
    assert emitted[-1]["valid_samples"] == 1
    assert emitted[-1]["tok_per_sec"] == 100
    # No persistence owner exists: the discarded instance's samples are simply gone.
    assert len(previous_aggregator.history_for("s", None)) == 1
