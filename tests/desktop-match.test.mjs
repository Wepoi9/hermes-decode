// Dependency-free test of the status-bar matcher: the repo ships desktop/
// plugin.js as a SINGLE plain-ESM file the desktop reads verbatim, so this
// test extracts the MATCHER block from the same file the app loads instead
// of importing the plugin (its `@hermes/plugin-sdk` import is app-owned).
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'

const source = await readFile(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'desktop', 'plugin.js'),
  'utf8'
)
const block = source.match(/\/\/ MATCHER-BEGIN[^\n]*\n([\s\S]*?)\n\/\/ MATCHER-END/)
assert.ok(block, 'plugin.js must contain the MATCHER-BEGIN..MATCHER-END block')
const code = block[1].replace(/export function/, 'function')
const pickVisibleSample = new Function(`${code}; return pickVisibleSample`)()
assert.equal(typeof pickVisibleSample, 'function')
const cacheBlock = source.match(/\/\/ CACHE-BEGIN[^\n]*\n([\s\S]*?)\n\/\/ CACHE-END/)
assert.ok(cacheBlock, 'plugin.js must contain the CACHE-BEGIN..CACHE-END block')
const cacheCode = cacheBlock[1].replace(/export function/, 'function')
const applyIncomingSample = new Function(`${cacheCode}; return applyIncomingSample`)()
assert.equal(typeof applyIncomingSample, 'function')

// The handler stores samples newest-first. Samples carry (sessionId, conn).
const newer = { sessionId: 's-focused', provider: 'p1', model: 'm', conn: 'c-a', tokPerSecond: 120 }
const older = { sessionId: 's-focused', provider: 'p1', model: 'm', conn: 'c-a', tokPerSecond: 80 }
const otherSession = { sessionId: 's-other', provider: 'p1', model: 'm', conn: 'c-a', tokPerSecond: 300 }
const otherConn = { sessionId: 's-focused', provider: 'p2', model: 'm', conn: 'c-b', tokPerSecond: 250 }
const noConn = { sessionId: 's-focused', provider: 'p1', model: 'm', conn: null, tokPerSecond: 90 }
const otherModel = { sessionId: 's-focused', provider: 'p1', model: 'other-model', conn: 'c-a', tokPerSecond: 400 }

// The readout follows the FOCUSED session only: the newest measurement for
// that session is the readout (the matcher never narrows by model; see below).
assert.equal(pickVisibleSample([newer, older], 's-focused'), newer)
assert.equal(pickVisibleSample([older], 's-focused'), older)

// Sessions share nothing: switching focus switches the reference, and a fresh
// session (or a tab whose samples belong to another session) shows '—'.
assert.equal(pickVisibleSample([otherSession], 's-focused'), null)
assert.equal(pickVisibleSample([newer, older], 's-tab-b'), null)
assert.equal(pickVisibleSample([otherSession, newer], 's-focused'), newer)

// The same session reached over two endpoints is ambiguous: show '—', never a
// speed that belongs to a connection the user may not be using.
assert.equal(pickVisibleSample([newer, otherConn], 's-focused'), null)
assert.equal(pickVisibleSample([otherConn, newer, older], 's-focused'), null)

// A sample the payload carries no endpoint identity for stays ambiguous when
// it mixes with a known endpoint: mixed known/unknown never borrows the known
// endpoint's speed. Alone, that consistent window is readable.
assert.equal(pickVisibleSample([noConn, newer], 's-focused'), null)
assert.equal(pickVisibleSample([noConn], 's-focused'), noConn)

// The matcher never narrows by model: the host cannot resolve the focused
// session's own model, and borrowing the main model's slug for that would
// point a tab's readout at a model it is not running. So the newest sample of
// the focused session wins regardless of which model recorded it — per-model
// freshness lives in the windows (python resets the decode history on a model
// change and a new-model sample retires that session's stale-model rows in the
// cache), which is what keeps a stale-model speed off the readout.
assert.equal(pickVisibleSample([otherModel], 's-focused'), otherModel)
assert.equal(pickVisibleSample([newer, otherModel], 's-focused'), newer)
assert.equal(pickVisibleSample([otherModel, newer], 's-focused'), otherModel)

// No focused session (null/empty), malformed input, or junk entries never crash the readout.
assert.equal(pickVisibleSample([newer], null), null)
assert.equal(pickVisibleSample([newer], ''), null)
assert.equal(pickVisibleSample([], 's-focused'), null)
assert.equal(pickVisibleSample(null, 's-focused'), null)
assert.equal(pickVisibleSample([null, newer, older], 's-focused'), newer)

// The desktop cache keeps one row per (session, endpoint) window, replacing the
// stale aggregate for that window only.
const oldRow = { sessionId: 's', provider: 'p1', model: 'm', conn: 'c-a', tokPerSecond: 80 }
const newRow = { sessionId: 's', provider: 'p1', model: 'm', conn: 'c-a', tokPerSecond: 120 }
assert.deepEqual(applyIncomingSample([oldRow], { sessionId: 's', conn: 'c-a' }, newRow, 'm'), [newRow])

// A sample under a new model retires the SAME session's stale-model rows in
// one shot — and never touches another session's rows.
const staleRow = { sessionId: 's', provider: 'p1', model: 'old-model', conn: 'c-a', tokPerSecond: 90 }
const otherSessionRow = { sessionId: 's-other', provider: 'p1', model: 'old-model', conn: 'c-a', tokPerSecond: 70 }
const newModelSample = { sessionId: 's', provider: 'p1', model: 'new-model', conn: 'c-b', tokPerSecond: 110 }
assert.deepEqual(
  applyIncomingSample([staleRow, otherSessionRow], { sessionId: 's', conn: 'c-b' }, newModelSample, 'new-model'),
  [newModelSample, otherSessionRow]
)

// An unmeasured call under the session's current model: its own window's row
// goes and the stale-model rows retire with it — nothing lingers as a row.
assert.deepEqual(
  applyIncomingSample([staleRow, otherSessionRow], { sessionId: 's', conn: 'c-a' }, null, 'new-model'),
  [otherSessionRow]
)

// The row list is bounded to the same eight windows as the python LRU.
const many = Array.from({ length: 9 }, (_unused, index) => ({
  sessionId: `s-${index}`, provider: 'p', model: 'm', conn: null, tokPerSecond: 50 + index
}))
const bounded = applyIncomingSample(many, { sessionId: 's-new', conn: null }, {
  sessionId: 's-new', provider: 'p', model: 'm', conn: null, tokPerSecond: 111
}, 'm')
assert.equal(bounded.length, 8)
assert.equal(bounded[0].tokPerSecond, 111)
assert.ok(!bounded.some(item => item.sessionId === 's-8'))

// A backend restart regenerates the connection identity, so the same session
// would otherwise keep its pre-restart rows under a stale endpoint forever.
// A sample under the newer generation retires the SAME session's pre-restart
// rows so the readout is not stuck on a stale endpoint; another session's rows
// are never touched, and a same-generation row is replaced without collateral.
const preRestartRow = { sessionId: 's', provider: 'p1', model: 'm', conn: 'c-a', gen: 'g-old', tokPerSecond: 80 }
const otherSessionPreRow = { sessionId: 's-other', provider: 'p1', model: 'm', conn: 'c-a', gen: 'g-old', tokPerSecond: 70 }
const postRestartSample = { sessionId: 's', provider: 'p1', model: 'm', conn: 'c-b', gen: 'g-new', tokPerSecond: 120 }
assert.deepEqual(
  applyIncomingSample([preRestartRow, otherSessionPreRow], { sessionId: 's', conn: 'c-b', gen: 'g-new' }, postRestartSample, 'm'),
  [postRestartSample, otherSessionPreRow]
)
// Even an unmeasured call under the new generation clears the stale endpoint.
assert.deepEqual(
  applyIncomingSample([preRestartRow], { sessionId: 's', conn: 'c-b', gen: 'g-new' }, null, 'm'),
  []
)
// Same generation: the window's own row is replaced, nothing else retires.
const sameGenRow = { sessionId: 's', provider: 'p1', model: 'm', conn: 'c-a', gen: 'g-new', tokPerSecond: 80 }
const sameGenSample = { sessionId: 's', provider: 'p1', model: 'm', conn: 'c-a', gen: 'g-new', tokPerSecond: 120 }
assert.deepEqual(
  applyIncomingSample([sameGenRow], { sessionId: 's', conn: 'c-a', gen: 'g-new' }, sameGenSample, 'm'),
  [sameGenSample]
)
// After the restart retires the pre-restart row, the window holds one endpoint
// and reads its speed (it is never forced to '—' by a stale endpoint it no longer has).
assert.equal(pickVisibleSample([postRestartSample], 's'), postRestartSample)

// The bar item registers as a toggleable DATA contribution: `data` IS the
// bar's own StatusbarItem (its id, the menu name, and the render live inside
// it), so the bar's right-click "Show in status bar" menu lists it and the
// user hides/restores it there. A top-level render would lock the item to the
// bar forever — the host ignores `data` the moment a contribution carries its
// own render, and an item with no menu name never appears in that menu.
const registerBlock = source.match(/\/\/ REGISTER-BEGIN[^\n]*\n([\s\S]*?)\n[ \t]*\/\/ REGISTER-END/)
assert.ok(registerBlock, 'plugin.js must contain the REGISTER-BEGIN..REGISTER-END block')
const registration = registerBlock[1]
assert.match(registration, /id:\s*'decode-speed'/)
assert.match(registration, /data:\s*\{/)
assert.match(registration, /id:\s*'hermes-decode-speed'/)
assert.match(registration, /toggleLabel:\s*'デコード速度'/)
// The registration declares exactly one render and it lives INSIDE `data` —
// never a top-level render slot that would ignore the menu and lock the item.
assert.equal((registration.match(/render:/g) || []).length, 1)
const beforeData = registration.slice(
  registration.indexOf('ctx.register'),
  registration.indexOf('data:')
)
assert.ok(!beforeData.includes('render:'), 'the item render must live inside data, not at the top level')
// The readout keeps the core item's own icon and label notation (Zap icon and
// `… t/s`), showing '—' while nothing is measured yet.
assert.match(source, /icons\.Zap/)
assert.match(source, /t\/s/)

console.log('desktop matcher tests passed')
