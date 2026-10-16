import importlib.util
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


def test_unmeasured_samples_stay_in_window_but_not_sum():
    state = plugin.DecodeAggregator()
    state.record("s", "m", None, {"output_tokens": 300}, 3)
    result = state.record("s", "m", None, None, None)
    assert result == {"tok_per_sec": 100, "valid_samples": 1, "measured": True}
    assert len(state.history_for("s", None)) == 2


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


def test_conn_identity_is_derived_and_opaque():
    derived = plugin._conn_id("https://private.invalid")
    # Same endpoint, one identity inside one process — window keys stay stable.
    assert derived == plugin._conn_id("https://private.invalid")
    assert derived == plugin._conn_id("https://private.invalid/")
    assert derived == plugin._conn_id("https://private.invalid ")
    assert derived != plugin._conn_id("https://other.invalid")
    assert len(derived) == plugin.CONNAUTH_ID_LENGTH
    # The URL itself never leaks into the derived identity.
    assert "private" not in derived and "invalid" not in derived
    assert plugin._conn_id(None) is None
    assert plugin._conn_id("") is None
    assert plugin._conn_id("   / ") is None


def test_conn_identity_is_not_globally_deterministic():
    # A salt-less deterministic digest would let anyone match candidate URLs
    # against a published identity; the identity is keyed per process instead,
    # so a fresh import (a plugin reload, a new process) derives its own value
    # while staying stable for that process.
    fresh_spec = importlib.util.spec_from_file_location("hermes_decode_conn_fresh", MODULE_PATH)
    fresh = importlib.util.module_from_spec(fresh_spec)
    fresh_spec.loader.exec_module(fresh)
    assert fresh._conn_id("https://private.invalid") != plugin._conn_id("https://private.invalid")
    assert fresh._conn_id("https://private.invalid") == fresh._conn_id("https://private.invalid ")


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
    assert payload == {"schema_version": 1, "session_id": "session", "provider": "provider",
                       "model": "model", "conn": plugin._conn_id("https://private.invalid"),
                       "gen": plugin._PROCESS_GENERATION,
                       "tok_per_sec": 100, "measured": True, "valid_samples": 1}


def test_generation_id_tracks_the_process_lifecycle():
    # A backend restart (or a plugin reload: a fresh import) regenerates the
    # connection identity's key, so the same session would otherwise keep its
    # pre-restart rows under a stale endpoint and show '—' forever. The payload
    # carries one opaque generation id per process so the desktop can tell a
    # restarted backend's samples from the previous run's and drop the stale
    # rows: stable for the life of this process, different for the next one.
    assert plugin._PROCESS_GENERATION == plugin._PROCESS_GENERATION
    assert len(plugin._PROCESS_GENERATION) == plugin.PROCESS_GENERATION_LENGTH
    fresh_spec = importlib.util.spec_from_file_location("hermes_decode_gen_fresh", MODULE_PATH)
    fresh = importlib.util.module_from_spec(fresh_spec)
    fresh_spec.loader.exec_module(fresh)
    assert fresh._PROCESS_GENERATION != plugin._PROCESS_GENERATION
    assert fresh._PROCESS_GENERATION == fresh._PROCESS_GENERATION


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
    # The model bookkeeping is bounded the same way, and an evicted session
    # loses its windows: nothing accumulates without a ceiling.
    assert len(state._models) == 8
    assert "s-0" not in state._models


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

    def hammer(index):
        session, conn = keys[index]
        barrier.wait()
        for call_index in range(calls_per_key):
            result = state.record(session, f"m-{index}", conn, {"output_tokens": 10}, 1)
            assert result["valid_samples"] == min(call_index + 1, plugin.WINDOW_SIZE)

    def shared_hammer():
        barrier.wait()
        for _ in range(calls_per_key):
            state.record("shared", "m-shared", None, {"output_tokens": 10}, 1)

    barrier = threading.Barrier(len(keys) + 2)
    threads = [threading.Thread(target=hammer, args=(index,)) for index in range(len(keys))]
    threads.append(threading.Thread(target=shared_hammer))
    threads.append(threading.Thread(target=shared_hammer))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Concurrent calls never lost an append or blurred a window: every hammered
    # window holds exactly its last ten samples, and the LRU cap stayed honest.
    assert state.retained_window_count == 7
    for session, conn in keys:
        window = state.history_for(session, conn)
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
