import { STATUSBAR_AREAS, Tip, atom, computed, host, icons, useValue } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

const ID = 'hermes-decode'
// CONTRACT-BEGIN (tests extract this declaration to check the Python contract)
const SCHEMA_VERSION = 3
// CONTRACT-END
const DESCRIPTION = 'フォーカス中のセッションの累積デコード速度（gateway再起動でリセット）'
const sessionRows = atom([])

// MATCHER-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function pickVisibleSample(rows, sessionId) {
  if (!Array.isArray(rows) || typeof sessionId !== 'string' || !sessionId) return null
  return rows.find(item => item && item.sessionId === sessionId) || null
}
// MATCHER-END
// CACHE-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function applyIncomingSample(rows, sessionId, sample) {
  const others = (Array.isArray(rows) ? rows : []).filter(item => item && item.sessionId !== sessionId)
  return sample ? [sample, ...others] : others
}
// CACHE-END

const visibleSample = computed(
  [sessionRows, host.state.focusedStoredSessionId],
  (rows, sessionId) => pickVisibleSample(rows, sessionId)
)

function DecodeSpeed() {
  const sample = useValue(visibleSample)
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
  const { session_id: sessionId, provider, model, schema_version: version } = payload
  if (version !== SCHEMA_VERSION || typeof sessionId !== 'string' || !sessionId ||
      typeof provider !== 'string' || !provider || typeof model !== 'string' || !model ||
      typeof payload.measured !== 'boolean') return null

  const speed = payload.tok_per_sec
  const count = payload.valid_samples
  if (!Number.isSafeInteger(count) || count < 0) return null
  if (count === 0) {
    if (payload.measured || speed !== null) return null
    return { sessionId, sample: null }
  }
  if (typeof speed !== 'number' || !Number.isFinite(speed) || speed <= 0) return null
  // An unmeasured call does not erase the cumulative speed of earlier calls.
  return { sessionId, sample: { sessionId, tokPerSecond: speed } }
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

    // The event name is namespaced by this plugin's id, matching the python
    // half's broadcast_plugin_event(PLUGIN_ID, 'sample', payload).
    host.onEvent(`plugin.${ID}.sample`, event => {
      const parsed = parseSample(event)
      if (!parsed) return
      sessionRows.set(applyIncomingSample(sessionRows.get(), parsed.sessionId, parsed.sample))
    })
  }
}
