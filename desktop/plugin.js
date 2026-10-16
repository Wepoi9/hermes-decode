import { STATUSBAR_AREAS, Tip, atom, computed, host, icons, useValue } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

const ID = 'hermes-decode'
// ホバー説明: 直近10回のデコード速度だとだけ書く（履歴値である説明は泡では繰り返さない）。
const DESCRIPTION = 'フォーカス中のセッションの直近10回のデコード速度'
const latestSamples = atom([])
// Samples arrive per (session, endpoint), newest-first, and the python window is
// keyed the same way: another session never borrows a measurement, and one
// session never mixes two connection endpoints into one aggregate. The readout
// follows the FOCUSED stored session — switching sessions switches the
// reference, a fresh session shows '—' until its first completed call. The SDK
// does not hand a plugin the focused session's own model, so the readout never
// narrows by model: the newest measurement of the focused session wins, and
// per-model freshness stays in the windows — a model change clears that
// session's decode history on the python side, and a sample under the new
// model retires that session's stale model rows from the desktop cache in the
// same update. conn is an opaque endpoint identity (never a URL): samples whose
// endpoints cannot be told apart show '—' instead of a wrong speed. A backend
// restart regenerates that identity, so every sample also carries a process
// generation id and a sample under the newer generation retires the same
// session's pre-restart rows before the readout looks at them.
// MATCHER-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function pickVisibleSample(samples, sessionId) {
  if (!Array.isArray(samples) || typeof sessionId !== 'string' || !sessionId) return null
  const matches = samples.filter(item => item && item.sessionId === sessionId)
  if (!matches.length) return null
  // One endpoint for these samples: the newest measurement is the readout.
  // Mixed endpoints (or a sample the payload carries no endpoint identity for)
  // stay ambiguous: show '—', never a speed from a connection the user may not be using.
  const endpoints = new Set(matches.map(item => (typeof item.conn === 'string' && item.conn) || ''))
  return endpoints.size === 1 ? matches[0] : null
}
// MATCHER-END
// CACHE-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function applyIncomingSample(rows, key, sample, model) {
  // One row per (session, endpoint) window: the incoming sample replaces its row.
  const sameKey = item => item && item.sessionId === key.sessionId && item.conn === key.conn
  // A sample under a new model retires the SAME session's stale-model rows in
  // the same shot — the python window resets on a model change and the desktop
  // cache follows it; another session's rows are never touched.
  const staleModel = item => Boolean(model) && item && item.sessionId === key.sessionId && item.model !== model
  // A backend restart regenerates the connection identity, so the same session
  // would otherwise keep its pre-restart rows under a stale endpoint forever
  // and the readout would stay '—'. A sample under a newer generation retires
  // the SAME session's older-generation rows in the same shot; another
  // session's rows are never touched, and a known-generation row is kept.
  const staleGeneration = item => Boolean(key && key.gen) && item && item.sessionId === key.sessionId && item.gen !== key.gen
  // The row list stays bounded like the python window LRU: eight windows, no more.
  return [
    ...(sample ? [sample] : []),
    ...rows.filter(item => !sameKey(item) && !staleModel(item) && !staleGeneration(item))
  ].slice(0, 8)
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

function parseSample(event) {
  if (!event || typeof event !== 'object' || !event.payload || typeof event.payload !== 'object') return null
  const payload = event.payload
  const sessionId = payload.session_id
  const provider = payload.provider
  const model = payload.model
  const version = payload.schema_version
  const conn = typeof payload.conn === 'string' && payload.conn ? payload.conn : null
  // The process generation names the backend process that produced this sample.
  // A restart regenerates it; a newer generation retires the same session's
  // older-generation rows so the readout is not stuck on a pre-restart endpoint.
  const gen = typeof payload.gen === 'string' && payload.gen ? payload.gen : null

  if (version !== 1 || typeof sessionId !== 'string' || !sessionId || typeof provider !== 'string' || !provider || typeof model !== 'string' || !model) return null
  // The window is per (session, endpoint): one row per live window, newest wins.
  const key = { sessionId, conn, gen }
  if (!payload.measured) {
    // 未計測の呼び出しが窓を空にした: 表示値を残さず未計測へ戻す。
    return { key, sample: null, model }
  }
  const speed = payload.tok_per_sec
  const sampleCount = payload.valid_samples
  if (!Number.isInteger(sampleCount) || sampleCount <= 0) return null
  if (typeof speed !== 'number' || !Number.isFinite(speed) || speed <= 0) return null

  return { key, sample: { sessionId, provider, model, conn, gen, tokPerSecond: speed }, model }
}

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
      const { key, sample, model } = parsed
      // One row per (session, endpoint) window; a sample under a new model
      // retires that session's stale-model rows in the same update.
      latestSamples.set(applyIncomingSample(latestSamples.get(), key, sample, model))
    })
  }
}
