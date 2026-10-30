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
    session-wide usage counters. A compaction that rotates the session id moves
    the total onto the session that now holds the transcript. State lasts until
    the plugin/gateway restarts; it is not reconstructed from the session log.
    """

    def __init__(self):
        self._totals = {}
        # Last identity seen for a session so a compaction carry-over can still
        # name provider/model, which the event contract requires. Not a key.
        self._identity = {}
        self._lock = threading.Lock()

    @staticmethod
    def _speed(total_tokens, total_seconds, count):
        """Round the cumulative ratio, or return None when nothing was measured."""
        if not count or total_seconds <= 0:
            return None
        rate = total_tokens / total_seconds
        if not math.isfinite(rate) or rate <= 0:
            return None
        return int(math.floor(rate + 0.5))

    def record(self, session, usage, decode_seconds, provider=None, model=None):
        """Include a completed, measured API call in the session total."""
        tokens = _output_tokens(usage)
        seconds = _positive_finite(decode_seconds)
        measured = tokens is not None and seconds is not None
        with self._lock:
            if isinstance(provider, str) and provider and isinstance(model, str) and model:
                self._identity[session] = (provider, model)
            total_tokens, total_seconds, count = self._totals.get(session, (0.0, 0.0, 0))
            if measured:
                next_tokens = total_tokens + tokens
                next_seconds = total_seconds + seconds
                if math.isfinite(next_tokens) and math.isfinite(next_seconds):
                    total_tokens, total_seconds, count = next_tokens, next_seconds, count + 1
                else:
                    measured = False
            self._totals[session] = (total_tokens, total_seconds, count)

            return {
                "tok_per_sec": self._speed(total_tokens, total_seconds, count),
                "valid_samples": count,
                "measured": measured,
            }

    def carry_over(self, old_session, new_session):
        """Move a session's cumulative total onto the session holding the transcript.

        ``post_compaction`` reports the previous id only when the transcript
        rotated, so in-place compaction never reaches here with a distinct pair.
        The old entry is dropped on the way out: a repeat report carries nothing
        and cannot double-count. A child that already measured calls keeps both
        segments, because they belong to one conversation's cumulative total.
        """
        with self._lock:
            moved = self._totals.pop(old_session, None)
            if moved is None:
                return None
            tokens, seconds, count = moved
            current_tokens, current_seconds, current_count = self._totals.get(
                new_session, (0.0, 0.0, 0)
            )
            total_tokens = tokens + current_tokens
            total_seconds = seconds + current_seconds
            total_count = count + current_count
            if not (math.isfinite(total_tokens) and math.isfinite(total_seconds)):
                self._totals[old_session] = moved
                return None
            self._totals[new_session] = (total_tokens, total_seconds, total_count)
            identity = self._identity.get(new_session) or self._identity.get(old_session)
            self._identity.pop(old_session, None)
            if identity is None or not total_count:
                return None
            return {
                "tok_per_sec": self._speed(total_tokens, total_seconds, total_count),
                "valid_samples": total_count,
                "provider": identity[0],
                "model": identity[1],
            }

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
        session_id, kwargs.get("usage"), kwargs.get("decode_seconds"), provider, model
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


def post_compaction(**kwargs):
    """Carry the cumulative total onto the session the transcript rotated to.

    The event is not a measured call, so it reports ``measured: False`` while
    still publishing the cumulative speed; the Desktop half keeps showing that
    value instead of blanking the readout after the rotation.
    """
    session_id = kwargs.get("session_id")
    old_session_id = kwargs.get("old_session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    if not isinstance(old_session_id, str) or not old_session_id or old_session_id == session_id:
        return

    carried = _AGGREGATOR.carry_over(old_session_id, session_id)
    if carried is None:
        return
    _broadcast("sample", {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "provider": carried["provider"],
        "model": carried["model"],
        "tok_per_sec": carried["tok_per_sec"],
        "measured": False,
        "valid_samples": carried["valid_samples"],
    })


def register(ctx):
    """Register the observer hooks: completed calls and committed compactions."""
    ctx.register_hook("post_api_request", post_api_request)
    ctx.register_hook("post_compaction", post_compaction)
