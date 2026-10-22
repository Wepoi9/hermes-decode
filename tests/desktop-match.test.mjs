// Dependency-free test of the status-bar readout: the repo ships desktop/
// plugin.js as a SINGLE plain-ESM file the desktop reads verbatim, so this
// test extracts the CONTRACT, MATCHER and CACHE blocks from the same file the
// app loads instead of importing the plugin (its `@hermes/plugin-sdk` import is
// app-owned). It also checks the caps the two halves declare separately still
// agree with `__init__.py`.
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
// The shared caps live in the CONTRACT block the extracted functions read from.
const contractBlock = source.match(/\/\/ CONTRACT-BEGIN[^\n]*\n([\s\S]*?)\n\/\/ CONTRACT-END/)
assert.ok(contractBlock, 'plugin.js must contain the CONTRACT-BEGIN..CONTRACT-END block')
const contract = contractBlock[1]
const pickVisibleSample = new Function(`${contract}; ${code}; return pickVisibleSample`)()
assert.equal(typeof pickVisibleSample, 'function')
const cacheBlock = source.match(/\/\/ CACHE-BEGIN[^\n]*\n([\s\S]*?)\n\/\/ CACHE-END/)
assert.ok(cacheBlock, 'plugin.js must contain the CACHE-BEGIN..CACHE-END block')
const cacheCode = cacheBlock[1].replace(/export function/, 'function')
const applyIncomingSample = new Function(`${contract}; ${cacheCode}; return applyIncomingSample`)()
assert.equal(typeof applyIncomingSample, 'function')
const parseBlock = source.match(/\/\/ PARSE-BEGIN[^\n]*\n([\s\S]*?)\n\/\/ PARSE-END/)
assert.ok(parseBlock, 'plugin.js must contain the PARSE-BEGIN..PARSE-END block')
const parseSample = new Function(`${contract}; ${parseBlock[1]}; return parseSample`)()
assert.equal(typeof parseSample, 'function')

// The two halves cannot import each other, so the caps are declared twice; this is the
// drift guard: the Desktop half must still answer to the Python half's numbers.
const pythonSource = await readFile(
  join(dirname(fileURLToPath(import.meta.url)), '..', '__init__.py'),
  'utf8'
)
const declared = (source_, label, pattern) => {
  const found = new RegExp(pattern, 'm').exec(source_)
  assert.ok(found, `${label} must be declared`)
  return Number(found[1])
}
assert.equal(declared(contract, 'desktop WINDOW_SIZE', '^const WINDOW_SIZE = (\\d+)'),
             declared(pythonSource, 'python WINDOW_SIZE', '^WINDOW_SIZE = (\\d+)'))
assert.equal(declared(contract, 'desktop MAX_ROWS', '^const MAX_ROWS = (\\d+)'),
             declared(pythonSource, 'python MAX_DECODE_WINDOWS', '^MAX_DECODE_WINDOWS = (\\d+)'))
assert.equal(declared(contract, 'desktop SCHEMA_VERSION', '^const SCHEMA_VERSION = (\\d+)'),
             declared(pythonSource, 'python SCHEMA_VERSION', '^SCHEMA_VERSION = (\\d+)'))

// The python half stopped publishing an endpoint identity and a process
// generation id: the Desktop half must not read them either, or it would keep
// waiting for a field the payload no longer carries.
assert.ok(!source.includes('payload.conn'), 'the payload carries no endpoint identity')
assert.ok(!source.includes('payload.gen'), 'the payload carries no process generation id')

// The handler stores one row per session, newest first.
const newer = { sessionId: 's-focused', provider: 'p1', model: 'm', tokPerSecond: 120 }
const older = { sessionId: 's-focused', provider: 'p1', model: 'm', tokPerSecond: 80 }
const otherSession = { sessionId: 's-other', provider: 'p1', model: 'm', tokPerSecond: 300 }
const otherModel = { sessionId: 's-focused', provider: 'p1', model: 'other-model', tokPerSecond: 400 }

// The readout follows the FOCUSED session only: the newest measurement for
// that session is the readout (the matcher never narrows by model; see below).
assert.equal(pickVisibleSample([newer, older], 's-focused'), newer)
assert.equal(pickVisibleSample([older], 's-focused'), older)

// Sessions share nothing: switching focus switches the reference, and a fresh
// session (or a tab whose samples belong to another session) shows '—'.
assert.equal(pickVisibleSample([otherSession], 's-focused'), null)
assert.equal(pickVisibleSample([newer, older], 's-tab-b'), null)
assert.equal(pickVisibleSample([otherSession, newer], 's-focused'), newer)

// A session that switched connection endpoint keeps ONE row: the newest
// measurement answers the readout instead of the session becoming ambiguous.
assert.equal(pickVisibleSample([newer], 's-focused'), newer)

// The matcher never narrows by model: the host cannot resolve the focused
// session's own model, and borrowing the main model's slug for that would
// point a tab's readout at a model it is not running. So the newest sample of
// the focused session wins regardless of which model recorded it — per-model
// freshness lives in the windows (python resets the decode history on a model
// change, so a new model's first sample carries its own history).
assert.equal(pickVisibleSample([otherModel], 's-focused'), otherModel)
assert.equal(pickVisibleSample([newer, otherModel], 's-focused'), newer)
assert.equal(pickVisibleSample([otherModel, newer], 's-focused'), otherModel)

// No focused session (null/empty), malformed input, or junk entries never crash the readout.
assert.equal(pickVisibleSample([newer], null), null)
assert.equal(pickVisibleSample([newer], ''), null)
assert.equal(pickVisibleSample([], 's-focused'), null)
assert.equal(pickVisibleSample(null, 's-focused'), null)
assert.equal(pickVisibleSample([null, newer, older], 's-focused'), newer)

// The desktop cache keeps ONE row per session: the incoming sample replaces
// that session's row, whichever connection endpoint recorded it.
const oldRow = { sessionId: 's', provider: 'p1', model: 'm', tokPerSecond: 80 }
const newRow = { sessionId: 's', provider: 'p2', model: 'm', tokPerSecond: 120 }
assert.deepEqual(applyIncomingSample([oldRow], 's', newRow), [newRow])

// Another session's rows are never touched.
const otherSessionRow = { sessionId: 's-other', provider: 'p1', model: 'm', tokPerSecond: 70 }
assert.deepEqual(applyIncomingSample([oldRow, otherSessionRow], 's', newRow), [newRow, otherSessionRow])

// The latest call measured nothing: that session's row goes, so the readout
// shows '—' instead of a speed the latest call did not measure. The history
// stays in the python windows and answers the next measured call.
assert.deepEqual(applyIncomingSample([oldRow, otherSessionRow], 's', null), [otherSessionRow])
assert.equal(pickVisibleSample(applyIncomingSample([oldRow], 's', null), 's'), null)

// The row list is bounded: at most one row per session, MAX_ROWS sessions.
const many = Array.from({ length: 9 }, (_unused, index) => ({
  sessionId: `s-${index}`, provider: 'p', model: 'm', tokPerSecond: 50 + index
}))
const bounded = applyIncomingSample(many, 's-new', {
  sessionId: 's-new', provider: 'p', model: 'm', tokPerSecond: 111
})
assert.equal(bounded.length, 8)
assert.equal(bounded[0].tokPerSecond, 111)
assert.ok(!bounded.some(item => item.sessionId === 's-8'))

// The payload decides the readout. A measured call carries a speed; an
// unmeasured call (the latest call measured nothing) clears the session.
const schemaVersion = declared(contract, 'desktop SCHEMA_VERSION', '^const SCHEMA_VERSION = (\\d+)')
const measuredFrame = {
  payload: {
    schema_version: schemaVersion, session_id: 's', provider: 'p', model: 'm',
    tok_per_sec: 120, measured: true, valid_samples: 3
  }
}
const unmeasuredFrame = {
  payload: {
    schema_version: schemaVersion, session_id: 's', provider: 'p', model: 'm',
    tok_per_sec: null, measured: false, valid_samples: 3
  }
}
assert.deepEqual(parseSample(measuredFrame), {
  sessionId: 's',
  sample: { sessionId: 's', provider: 'p', model: 'm', tokPerSecond: 120 }
})
assert.deepEqual(parseSample(unmeasuredFrame), { sessionId: 's', sample: null })

// A frame from another contract version is ignored: old and new events never mix.
assert.equal(parseSample({ payload: { ...unmeasuredFrame.payload, schema_version: schemaVersion - 1 } }), null)
// A measured frame without a usable speed or sample count is ignored too.
assert.equal(parseSample({ payload: { ...measuredFrame.payload, tok_per_sec: 0 } }), null)
assert.equal(parseSample({ payload: { ...measuredFrame.payload, tok_per_sec: Number.NaN } }), null)
assert.equal(parseSample({ payload: { ...measuredFrame.payload, valid_samples: 0 } }), null)
assert.equal(parseSample(null), null)
assert.equal(parseSample({}), null)

// End to end: the unmeasured call clears that session's readout, and the
// history it left behind answers the next measured call.
const cleared = applyIncomingSample([newRow], parseSample(unmeasuredFrame).sessionId, parseSample(unmeasuredFrame).sample)
assert.deepEqual(cleared, [])
assert.equal(pickVisibleSample(cleared, 's'), null)
const restored = applyIncomingSample(cleared, parseSample(measuredFrame).sessionId, parseSample(measuredFrame).sample)
assert.equal(pickVisibleSample(restored, 's').tokPerSecond, 120)

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
