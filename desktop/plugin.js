import { STATUSBAR_AREAS, Tip, atom, computed, host, useValue } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

const ID = 'hermes-decode'
// ホバー説明: 完了済み呼び出しの計測結果であって、生成中のリアルタイム速度ではないことを書く。
const DESCRIPTION = 'フォーカス中のセッションの直近10回の計測値（生成のリアルタイム速度ではない）'
const latestSamples = atom([])
// Samples arrive per (session, endpoint), newest-first, and the python window is
// keyed the same way: another session never borrows a measurement, and one
// session never mixes two connection endpoints into one aggregate. The readout
// follows the FOCUSED stored session — switching sessions switches the
// reference, a fresh session shows '—' until its first completed call. The host
// publishes only the MAIN model slug; the focused session's own model is not a
// door the SDK hands a plugin, so it stays unresolved and the readout never
// borrows the main model's slug to filter another tab's samples. Model-change
// freshness comes from the windows themselves: a model change clears that
// session's decode history on the python side, and a sample under the new
// model retires that session's stale model rows from the desktop cache in the
// same update. conn is an opaque endpoint identity (never a URL): samples whose
// endpoints cannot be told apart show '—' instead of a wrong speed. A backend
// restart regenerates that identity, so every sample also carries a process
// generation id and a sample under the newer generation retires the same
// session's pre-restart rows before the readout looks at them.
// MATCHER-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function pickVisibleSample(samples, sessionId, model) {
  if (!Array.isArray(samples) || typeof sessionId !== 'string' || !sessionId) return null
  // `model` is the focused session's OWN model when the host can resolve it,
  // and null when it cannot: an unresolved model never borrows another
  // session's — or the main model's — measurement to filter with.
  const knownModel = typeof model === 'string' && model ? model : null
  const matches = samples.filter(item => item && item.sessionId === sessionId &&
    (knownModel === null || (typeof item.model === 'string' && item.model === knownModel)))
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
// The SDK exposes the main model slug only; the focused session's own model is
// unresolved for a plugin, so the matcher runs without a model filter rather
// than borrowing the main model for a focused tab. (null = unresolved.)
const focusedSessionModel = null
const visibleSample = computed(
  [latestSamples, host.state.focusedStoredSessionId],
  (samples, sessionId) => pickVisibleSample(samples, sessionId, focusedSessionModel)
)

function DecodeSpeed() {
  const sample = useValue(visibleSample)
  const label = sample ? `↑ ${Math.trunc(sample.tokPerSecond)} tok/s` : '—'

  return jsx(Tip, {
    label: DESCRIPTION,
    children: jsx('span', {
      className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
      children: label
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
    ctx.register({
      id: 'decode-speed',
      area: STATUSBAR_AREAS.right,
      order: 130,
      render: () => jsx(DecodeSpeed, {})
    })

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
