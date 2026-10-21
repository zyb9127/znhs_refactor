import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { skillNameError } from '../frontend/src/utils/skillName.js'

const cases = JSON.parse(await readFile(new URL('./fixtures/skill_name_cases.json', import.meta.url), 'utf8'))
for (const { name, valid } of cases) {
  assert.equal(!skillNameError(name), valid, JSON.stringify(name))
}
assert.equal(skillNameError('中'.repeat(50)), '')
assert.ok(skillNameError('中'.repeat(51)))
for (const value of [null, 12, [], {}]) assert.ok(skillNameError(value))
console.log(`Frontend name validation passed: ${cases.length} naming cases, length boundaries and non-text input`)
