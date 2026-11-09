import assert from 'node:assert/strict'
import test from 'node:test'
import { shortVia } from './transactions.js'

test('목록용 결제 수단은 앞 단어 + 끝 4자리', () => {
  assert.equal(shortVia('신한 Deep Dream 체크 · ****1234'), '신한 1234')
  assert.equal(shortVia('국민 급여통장 · ****5678'), '국민 5678')
  assert.equal(shortVia('카드'), '카드')  // 끝자리를 모르면 이름 앞 단어만
  assert.equal(shortVia(undefined), undefined)
})
