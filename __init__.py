"""Hermes Decode plugin: session-cumulative decode throughput in memory."""

import math
from numbers import Real
import threading

PLUGIN_ID = "hermes-decode"
SCHEMA_VERSION = 3


def _positive_finite(value):
    """Return a positive finite float, or None for missing/invalid input."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _output_tokens(usage):
    """Read canonical output_tokens without guessing from response content."""
    if isinstance(usage, dict):
        value = usage.get("output_tokens")
    else:
        value = getattr(usage, "output_tokens", None)
    return _positive_finite(value)


class DecodeAggregator:
    """Accumulate measured decode tokens and seconds for each session.

    Model and connection changes remain in the same session total, matching
    session-wide usage counters. State lasts until the plugin/gateway restarts;
    it is not reconstructed from the session log.
    """

    def __init__(self):
        self._totals = {}
        self._lock = threading.Lock()

    def record(self, session, usage, decode_seconds):
        """Include a completed, measured API call in the session total."""
        tokens = _output_tokens(usage)
        seconds = _positive_finite(decode_seconds)
        measured = tokens is not None and seconds is not None
        with self._lock:
            total_tokens, total_seconds, count = self._totals.get(session, (0.0, 0.0, 0))
            if measured:
                next_tokens = total_tokens + tokens
                next_seconds = total_seconds + seconds
                if math.isfinite(next_tokens) and math.isfinite(next_seconds):
                    total_tokens, total_seconds, count = next_tokens, next_seconds, count + 1
                else:
                    measured = False
            self._totals[session] = (total_tokens, total_seconds, count)

            speed = None
            if count and total_seconds > 0:
                rate = total_tokens / total_seconds
                if math.isfinite(rate) and rate > 0:
                    speed = int(math.floor(rate + 0.5))
            return {"tok_per_sec": speed, "valid_samples": count, "measured": measured}

    def totals_for(self, session):
        """Return cumulative tokens, seconds and count for a session (tests)."""
        with self._lock:
            return self._totals.get(session, (0.0, 0.0, 0))


_AGGREGATOR = DecodeAggregator()


def _broadcast(event, payload):
    """Late import the Hermes event API so module import stays host-independent."""
    from hermes_cli.plugin_events import broadcast_plugin_event

    broadcast_plugin_event(PLUGIN_ID, event, payload)


def post_api_request(**kwargs):
    """Observe completed API calls; accept the full, evolving hook payload."""
    session_id = kwargs.get("session_id")
    provider = kwargs.get("provider")
    model = kwargs.get("model")
    if not all(isinstance(value, str) and value for value in (session_id, provider, model)):
        return

    result = _AGGREGATOR.record(
        session_id, kwargs.get("usage"), kwargs.get("decode_seconds")
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "provider": provider,
        "model": model,
        "tok_per_sec": result["tok_per_sec"],
        "measured": result["measured"],
        "valid_samples": result["valid_samples"],
    }
    _broadcast("sample", payload)


def register(ctx):
    """Register only the post_api_request observer hook."""
    ctx.register_hook("post_api_request", post_api_request)
