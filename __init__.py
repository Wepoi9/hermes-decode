"""Hermes Decode plugin: bounded, in-memory decode throughput aggregation."""

from collections import OrderedDict, deque
import hashlib
import hmac
import math
from numbers import Real
import os
import threading

PLUGIN_ID = "hermes-decode"
WINDOW_SIZE = 10
MAX_DECODE_WINDOWS = 8
SCHEMA_VERSION = 1
CONNAUTH_ID_LENGTH = 12
# Random once per process (and once per plugin reload, which re-imports this
# module): the connection identity is keyed by it, so no external observer can
# match a candidate URL against a published digest — the identity lives only in
# this process's memory and dies with it.
_CONNECTION_KEY = os.urandom(32)
# The same per-process randomness also names the process generation: a backend
# restart (or a plugin reload) regenerates the connection identity's key, so a
# restarted backend cannot be told apart from its own earlier run by the opaque
# endpoint ids alone. Publishing this one short id per call lets the desktop
# retire a session's pre-restart rows when it sees the next generation arrive,
# instead of keeping a stale endpoint alive and showing '—' forever.
PROCESS_GENERATION_LENGTH = 12
_PROCESS_GENERATION = hashlib.sha1(_CONNECTION_KEY).hexdigest()[:PROCESS_GENERATION_LENGTH]


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


def _conn_id(base_url):
    """Derive an opaque connection identity from the endpoint the hook carries.

    The Desktop never sees the URL itself: only this short keyed digest, so
    samples stay separable per real connection endpoint without leaking the
    endpoint. Root URLs are normalized (trim, drop the trailing slash,
    casefold) before the keyed hash; a missing or malformed URL carries no
    identity and stays None. A deterministic digest would let anyone match
    candidate URLs against a published identity, so the digest is an HMAC
    under the process-random key: same URL means same identity inside one
    process, and every process (reload, restart) derives its own value.
    """
    if not isinstance(base_url, str):
        return None
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        return None
    return hmac.new(
        _CONNECTION_KEY, normalized.casefold().encode("utf-8"), hashlib.sha1
    ).hexdigest()[:CONNAUTH_ID_LENGTH]


class DecodeAggregator:
    """Maintain last-ten windows per session and endpoint with a bounded LRU.

    The window is keyed by (session, connection endpoint): another session
    never borrows a measurement, and the same session never mixes two endpoints
    into one aggregate. A model change clears that session's decode history —
    decode speed leans on model performance, so yesterday's model's numbers
    must not answer today's model's readout.
    """

    def __init__(self):
        self._windows = OrderedDict()
        # Last seen model per session, kept to the same small LRU as the
        # windows: a session switching models resets its decode history.
        self._models = OrderedDict()
        # One small lock over the window bookkeeping: concurrent model calls
        # land on the same aggregator from several API threads, and the plugin
        # reloads without a server restart. The design stays in-memory.
        self._lock = threading.Lock()

    def _remember_model(self, session, model):
        # Callers must hold the lock (record is the only caller).
        # Returns True when this call starts a NEW model for the session.
        previous = self._models.get(session)
        self._models.pop(session, None)
        self._models[session] = model
        while len(self._models) > MAX_DECODE_WINDOWS:
            evicted, _ = self._models.popitem(last=False)
            self._drop_session_windows(evicted)
        return previous is not None and previous != model

    def _drop_session_windows(self, session):
        # Callers must hold the lock.
        for key in [key for key in self._windows if key[0] == session]:
            del self._windows[key]

    def _touch_window(self, session, conn):
        # Callers must hold the lock (record is the only caller).
        key = (session, conn)
        window = self._windows.pop(key, None)
        if window is None:
            window = deque(maxlen=WINDOW_SIZE)
        self._windows[key] = window
        while len(self._windows) > MAX_DECODE_WINDOWS:
            self._windows.popitem(last=False)
        return window

    def record(self, session, model, conn, usage, decode_seconds):
        """Append one completed API call; return its current aggregate."""
        with self._lock:
            if self._remember_model(session, model):
                # The session switched models: its decode history resets here,
                # so the aggregate below only ever answers the current model.
                self._drop_session_windows(session)
            window = self._touch_window(session, conn)
            tokens = _output_tokens(usage)
            seconds = _positive_finite(decode_seconds)
            window.append((tokens, seconds) if tokens is not None and seconds is not None else None)

            token_sum = 0.0
            seconds_sum = 0.0
            sample_count = 0
            for sample in window:
                if sample is None:
                    continue
                sample_tokens, sample_seconds = sample
                token_sum += sample_tokens
                seconds_sum += sample_seconds
                sample_count += 1

            speed = None
            if sample_count and seconds_sum > 0:
                rate = token_sum / seconds_sum
                if math.isfinite(rate) and rate > 0:
                    speed = int(math.floor(rate + 0.5))
            return {"tok_per_sec": speed, "valid_samples": sample_count, "measured": speed is not None}

    def history_for(self, session, conn):
        """Expose the bounded window for tests and diagnostic use."""
        with self._lock:
            window = self._windows.get((session, conn))
            return () if window is None else tuple(window)

    @property
    def retained_window_count(self):
        with self._lock:
            return len(self._windows)


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
    # Without this identifying tuple, we cannot safely attribute a sample or emit it.
    # The endpoint identity is derived here, from the hook's base_url, and stays
    # a short digest in the payload: the window is per (session, endpoint).
    if not all(isinstance(value, str) and value for value in (session_id, provider, model)):
        return
    conn = _conn_id(kwargs.get("base_url"))

    result = _AGGREGATOR.record(
        session_id, model, conn, kwargs.get("usage"), kwargs.get("decode_seconds")
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "provider": provider,
        "model": model,
        "conn": conn,
        "gen": _PROCESS_GENERATION,
        "tok_per_sec": result["tok_per_sec"],
        "measured": result["measured"],
        "valid_samples": result["valid_samples"],
    }
    _broadcast("sample", payload)


def register(ctx):
    """Register only the post_api_request observer hook."""
    ctx.register_hook("post_api_request", post_api_request)
