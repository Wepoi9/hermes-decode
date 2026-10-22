import { STATUSBAR_AREAS, Tip, atom, computed, host, icons, useValue } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

const ID = 'hermes-decode'
// CONTRACT-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim, injects it
// into the extracted matcher/cache blocks, and asserts these numbers still equal
// WINDOW_SIZE / MAX_DECODE_WINDOWS / SCHEMA_VERSION in __init__.py — the two halves
// cannot import each other, so this shared declaration is the drift guard)
const WINDOW_SIZE = 10
const MAX_ROWS = 8
const SCHEMA_VERSION = 2
// CONTRACT-END
// Hover tip: says only that the readout is the focused session's last WINDOW_SIZE decode
// speeds — the history explanation is not repeated in the tooltip.
const DESCRIPTION = `フォーカス中のセッションの直近${WINDOW_SIZE}回のデコード速度`
const latestSamples = atom([])
// One row per session, newest first. The python half keeps the per-endpoint
// windows and reports the readout of the call that just finished, so the
// desktop half only has to remember the newest result of each session: the
// focused session's newest row is the readout, another session's rows are
// never shown, and a session that switches connection endpoint simply
// replaces its own row instead of becoming ambiguous.
// MATCHER-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function pickVisibleSample(samples, sessionId) {
  if (!Array.isArray(samples) || typeof sessionId !== 'string' || !sessionId) return null
  return samples.find(item => item && item.sessionId === sessionId) || null
}
// MATCHER-END
// CACHE-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function applyIncomingSample(rows, sessionId, sample) {
  // One row per session: the incoming sample replaces that session's row.
  // An unmeasured call (sample === null) drops the row, so the readout shows
  // '—' instead of a speed the latest call did not measure.
  const others = rows.filter(item => !item || item.sessionId !== sessionId)
  return [...(sample ? [sample] : []), ...others].slice(0, MAX_ROWS)
}
// CACHE-END
// The SDK does not expose the focused session's own model, and borrowing the
// main model's slug for it would point a tab's readout at a model it is not
// running, so the matcher narrows by session only: the newest sample of the
// focused session wins regardless of which model recorded it.
const visibleSample = computed(
  [latestSamples, host.state.focusedStoredSessionId],
  (samples, sessionId) => pickVisibleSample(samples, sessionId)
)

function DecodeSpeed() {
  const sample = useValue(visibleSample)
  // The core status-bar item reads `105 t/s` with a Zap icon and shows '—'
  // while nothing is measured yet; the plugin keeps the identical icon and
  // label notation so hiding the core item and showing this one swaps the
  // readout, not the notation.
  const label = sample ? `${Math.round(sample.tokPerSecond)} t/s` : '—'

  return jsx(Tip, {
    label: DESCRIPTION,
    children: jsx('span', {
      className: 'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
      children: [jsx(icons.Zap, { className: 'size-3' }, 'icon'), jsx('span', { children: label }, 'speed')]
    })
  })
}

// PARSE-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
function parseSample(event) {
  if (!event || typeof event !== 'object' || !event.payload || typeof event.payload !== 'object') return null
  const payload = event.payload
  const sessionId = payload.session_id
  const provider = payload.provider
  const model = payload.model
  const version = payload.schema_version

  if (version !== SCHEMA_VERSION || typeof sessionId !== 'string' || !sessionId || typeof provider !== 'string' || !provider || typeof model !== 'string' || !model) return null
  if (!payload.measured) {
    // The latest call was not measured: clear that session's readout.
    return { sessionId, sample: null }
  }
  const speed = payload.tok_per_sec
  const sampleCount = payload.valid_samples
  if (!Number.isInteger(sampleCount) || sampleCount <= 0) return null
  if (typeof speed !== 'number' || !Number.isFinite(speed) || speed <= 0) return null

  return { sessionId, sample: { sessionId, provider, model, tokPerSecond: speed } }
}
// PARSE-END

export default {
  id: ID,
  name: 'Hermes Decode',
  defaultEnabled: false,
  register(ctx) {
    // REGISTER-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
    // The bar item registers as a DATA contribution: `data` IS the bar's own
    // StatusbarItem — its id, the bar's right-click menu name, and the render
    // live inside it, so the item appears under "Show in status bar" and the
    // user hides or restores it there like any core item (visibility persists
    // in the app's own status-bar preferences). A top-level render would lock
    // the item to the bar forever: the host ignores `data` the moment a
    // contribution carries its own render, and an item with no menu name is
    // never listed in that menu and always shows.
    ctx.register({
      id: 'decode-speed',
      area: STATUSBAR_AREAS.right,
      order: 130,
      data: {
        id: 'hermes-decode-speed',
        toggleLabel: 'デコード速度',
        render: () => jsx(DecodeSpeed, {})
      }
    })
    // REGISTER-END

    host.onEvent('plugin.hermes-decode.sample', event => {
      const parsed = parseSample(event)
      if (!parsed) return
      latestSamples.set(applyIncomingSample(latestSamples.get(), parsed.sessionId, parsed.sample))
    })
  }
}
