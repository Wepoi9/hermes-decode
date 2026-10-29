import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const source = await readFile(join(root, 'desktop', 'plugin.js'), 'utf8')
const python = await readFile(join(root, '__init__.py'), 'utf8')

function block(name) {
  const m = source.match(new RegExp(`// ${name}-BEGIN[^\\n]*\\n([\\s\\S]*?)\\n// ${name}-END`))
  assert.ok(m, `${name} block must exist`)
  return m[1]
}

const contract = block('CONTRACT')
const matcher = block('MATCHER').replace('export function', 'function')
const cache = block('CACHE').replace('export function', 'function')
const parser = block('PARSE')
const pickVisibleSample = new Function(`${contract}; ${matcher}; return pickVisibleSample`)()
const applyIncomingSample = new Function(`${contract}; ${cache}; return applyIncomingSample`)()
const parseSample = new Function(`${contract}; ${parser}; return parseSample`)()

const schema = Number(/SCHEMA_VERSION = (\d+)/.exec(contract)?.[1])
assert.ok(Number.isInteger(schema))
assert.equal(schema, Number(/SCHEMA_VERSION = (\d+)/.exec(python)?.[1]))

const s1 = { sessionId: 's1', tokPerSecond: 75 }
const s2 = { sessionId: 's2', tokPerSecond: 120 }
assert.equal(pickVisibleSample([s2, s1], 's1'), s1)
assert.equal(pickVisibleSample([s2, s1], 's3'), null)
assert.equal(pickVisibleSample([s1], null), null)
assert.equal(pickVisibleSample(null, 's1'), null)

const changed = { sessionId: 's1', tokPerSecond: 95 }
assert.deepEqual(applyIncomingSample([s1, s2], 's1', changed), [changed, s2])
assert.deepEqual(applyIncomingSample([s1, s2], 's1', null), [s2])
const many = Array.from({ length: 12 }, (_, i) => ({
  sessionId: `s-${i}`, tokPerSecond: i + 1
}))
assert.equal(applyIncomingSample(many, 'new', { sessionId: 'new', tokPerSecond: 99 }).length, 13)

const base = { schema_version: schema, session_id: 's1', provider: 'p', model: 'm' }
const measured = { payload: { ...base, tok_per_sec: 100, measured: true, valid_samples: 2 } }
const unmeasuredWithHistory = {
  payload: { ...base, tok_per_sec: 100, measured: false, valid_samples: 2 }
}
const unmeasuredWithoutHistory = {
  payload: { ...base, tok_per_sec: null, measured: false, valid_samples: 0 }
}
assert.deepEqual(parseSample(measured), { sessionId: 's1', sample: { sessionId: 's1', tokPerSecond: 100 } })
assert.deepEqual(parseSample(unmeasuredWithHistory), {
  sessionId: 's1', sample: { sessionId: 's1', tokPerSecond: 100 }
})
assert.deepEqual(parseSample(unmeasuredWithoutHistory), { sessionId: 's1', sample: null })
assert.equal(parseSample({ payload: { ...base, schema_version: 2, tok_per_sec: 100, measured: true, valid_samples: 2 } }), null)
assert.equal(parseSample({ payload: { ...base, tok_per_sec: 0, measured: true, valid_samples: 1 } }), null)
assert.equal(parseSample({ payload: { ...base, tok_per_sec: NaN, measured: true, valid_samples: 1 } }), null)
assert.equal(parseSample({ payload: { ...base, tok_per_sec: 1, measured: true, valid_samples: -1 } }), null)
assert.equal(parseSample({ payload: { ...base, tok_per_sec: 1, measured: true, valid_samples: 0 } }), null)
assert.equal(parseSample({ payload: { ...base, tok_per_sec: null, measured: false, valid_samples: 1 } }), null)
assert.equal(parseSample({ payload: { ...base, provider: '', tok_per_sec: 1, measured: true, valid_samples: 1 } }), null)
assert.equal(parseSample(null), null)

const bar = source.match(/\/\/ REGISTER-BEGIN[^\n]*\n([\s\S]*?)\n[ \t]*\/\/ REGISTER-END/)
assert.ok(bar, 'status bar registration exists')
assert.match(bar[1], /id:\s*'decode-speed'/)
assert.match(bar[1], /toggleLabel:\s*'デコード速度'/)
assert.equal((bar[1].match(/render:/g) || []).length, 1)
assert.match(source, /host\.onEvent\(`plugin\.\$\{ID\}\.sample`/)
assert.match(source, /icons\.Zap/)
console.log('desktop cumulative matcher tests passed')
