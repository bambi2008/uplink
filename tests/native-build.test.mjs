import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const bridge = await readFile(new URL('../mobile/native-bridge.js', import.meta.url), 'utf8')
const build = await readFile(new URL('../scripts/build-mobile.mjs', import.meta.url), 'utf8')
const config = JSON.parse(await readFile(new URL('../capacitor.config.json', import.meta.url), 'utf8'))

assert.match(build, /UPLINK_API_ORIGIN/)
assert.match(build, /\^https:/)
assert.doesNotMatch(config.server ? JSON.stringify(config.server) : '', /"url"/)
assert.equal(config.loggingBehavior, 'none')
assert.match(bridge, /SecureStorage/)
assert.match(bridge, /api\/account\/ws-ticket/)
assert.match(bridge, /encodeURIComponent\(payload\.ticket\)/)
assert.doesNotMatch(bridge, /MINIMAX_API_KEY|XF_APIKEY|DOUBAO_ACCESS_TOKEN/)

console.log('native mobile build contract tests passed')
