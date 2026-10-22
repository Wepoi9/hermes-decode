"""Hermes Decode plugin: bounded, in-memory decode throughput aggregation."""

from collections import OrderedDict, deque
import math
from numbers import Real
import threading

PLUGIN_ID = "hermes-decode"
# These caps and the payload keys are the contract with the Desktop half, which
# repeats them (desktop/plugin.js) because the two halves cannot import each
# other: tests/desktop-match.test.mjs asserts the numbers still match this file.
WINDOW_SIZE = 10          # completed calls kept per (session, endpoint) window
MAX_DECODE_WINDOWS = 8    # windows retained at once (LRU); the Desktop half caps
                          # the same number, but counts sessions, not windows
SCHEMA_VERSION = 2        # payload contract version the Desktop half checks


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


def _endpoint_key(base_url):
    """Normalize the endpoint the hook carries into this process's window key.

    The key only indexes the windows inside this process: the Desktop half
    never receives the URL, nor any digest of it, so nothing has to be hidden
    here. Root URLs are normalized (trim, drop the trailing slash, casefold)
    so the same endpoint always lands in the same window; a missing or
    malformed URL carries no key and stays None.
    """
    if not isinstance(base_url, str):
        return None
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        return None
    return normalized.casefold()


class DecodeAggregator:
    """Maintain last-ten windows per session and endpoint with a bounded LRU.

    The window is keyed by (session, connection endpoint): another session
    never borrows a measurement, and the same session never mixes two endpoints
    into one aggregate. A model change clears that session's decode history —
    decode speed leans on model performance, so yesterday's model's numbers
    must not answer today's model's readout. The endpoint key stays inside
    this process; the Desktop half only ever sees the readout.
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

    def _touch_window(self, session, endpoint):
        # Callers must hold the lock (record is the only caller).
        key = (session, endpoint)
        window = self._windows.pop(key, None)
        if window is None:
            window = deque(maxlen=WINDOW_SIZE)
        self._windows[key] = window
        while len(self._windows) > MAX_DECODE_WINDOWS:
            self._windows.popitem(last=False)
        return window

    def record(self, session, model, endpoint, usage, decode_seconds):
        """Append one completed API call; return this session's current readout.

        The aggregate covers the window's measured calls, but the readout
        belongs to the LATEST call: an unmeasured call reports no speed, and
        the history it leaves behind answers the next measured call.
        """
        with self._lock:
            if self._remember_model(session, model):
                # The session switched models: its decode history resets here,
                # so the aggregate below only ever answers the current model.
                self._drop_session_windows(session)
            window = self._touch_window(session, endpoint)
            tokens = _output_tokens(usage)
            seconds = _positive_finite(decode_seconds)
            measured = tokens is not None and seconds is not None
            window.append((tokens, seconds) if measured else None)

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
            if not measured:
                # The latest call measured nothing: no speed belongs to this
                # readout, however many earlier calls the window still holds.
                speed = None
            return {"tok_per_sec": speed, "valid_samples": sample_count, "measured": measured}

    def history_for(self, session, endpoint):
        """Expose the bounded window for tests and diagnostic use."""
        with self._lock:
            window = self._windows.get((session, endpoint))
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
    # The endpoint only keys this process's windows: it never reaches the
    # Desktop half, so the payload carries no URL, no digest of it and no
    # process-generation id.
    if not all(isinstance(value, str) and value for value in (session_id, provider, model)):
        return
    endpoint = _endpoint_key(kwargs.get("base_url"))

    result = _AGGREGATOR.record(
        session_id, model, endpoint, kwargs.get("usage"), kwargs.get("decode_seconds")
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
