import { STATUSBAR_AREAS, Tip, atom, computed, host, useValue } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

const ID = 'hermes-decode'
// ホバー説明: 完了済み呼び出しの計測結果であって、生成中のリアルタイム速度ではないことを書く。
const DESCRIPTION = 'フォーカス中のセッションの直近10回の計測値（生成のリアルタイム速度ではない）'
const latestSamples = atom([])
// Samples arrive per (session, endpoint), newest-first, and the python window is
// keyed the same way: another session never borrows a measurement, and one
// session never mixes two connection endpoints into one aggregate. The readout
// follows the FOCUSED stored session and the current model — switching sessions
// switches the reference, a fresh session shows '—' until its first completed
// call, and a model change clears that session's decode history on the python
// side. conn is an opaque endpoint identity (never a URL): samples whose
// endpoints cannot be told apart show '—' instead of a wrong speed. The host
// publishes the model slug only, so the readout never borrows another model's
// measurement either.
// MATCHER-BEGIN (tests/desktop-match.test.mjs extracts this block verbatim)
export function pickVisibleSample(samples, sessionId, model) {
  if (!Array.isArray(samples) || typeof sessionId !== 'string' || !sessionId || typeof model !== 'string' || !model) return null
  const matches = samples.filter(item => item && item.sessionId === sessionId && typeof item.model === 'string' && item.model === model)
  if (!matches.length) return null
  // One endpoint for these samples: the newest measurement is the readout.
  // Mixed endpoints (or a sample the payload carries no endpoint identity for)
  // stay ambiguous: show '—', never a speed from a connection the user may not be using.
  const endpoints = new Set(matches.map(item => (typeof item.conn === 'string' && item.conn) || ''))
  return endpoints.size === 1 ? matches[0] : null
}
// MATCHER-END
const visibleSample = computed(
  [latestSamples, host.state.model, host.state.focusedStoredSessionId],
  (samples, model, sessionId) => pickVisibleSample(samples, sessionId, model)
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

  if (version !== 1 || typeof sessionId !== 'string' || !sessionId || typeof provider !== 'string' || !provider || typeof model !== 'string' || !model) return null
  // The window is per (session, endpoint): one row per live window, newest wins.
  const key = { sessionId, conn }
  if (!payload.measured) {
    // 未計測の呼び出しが窓を空にした: 表示値を残さず未計測へ戻す。
    return { key, sample: null }
  }
  const speed = payload.tok_per_sec
  const sampleCount = payload.valid_samples
  if (!Number.isInteger(sampleCount) || sampleCount <= 0) return null
  if (typeof speed !== 'number' || !Number.isFinite(speed) || speed <= 0) return null

  return { key, sample: { sessionId, provider, model, conn, tokPerSecond: speed } }
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
      const { key, sample } = parsed
      const sameKey = item => item.sessionId === key.sessionId && item.conn === key.conn
      // One row per (session, endpoint) window, replacing the stale aggregate;
      // the row list stays bounded like the python window LRU (8 windows).
      latestSamples.set([
        ...(sample ? [sample] : []),
        ...latestSamples.get().filter(item => !sameKey(item))
      ].slice(0, 16))
    })
  }
}
