// 다시 불러올 때 카드 비밀번호 자리수 (현대 4자리 · 하나 · 국민 앞 2자리 · 그 밖은 2 또는 4자리)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cardPw } from './institutions.js'

test('카드사별 카드 비밀번호 안내와 검사', () => {
  assert.deepEqual([cardPw('0302').hint, cardPw('0302').ok('1234'), cardPw('0302').ok('12')], [' 4자리', true, false])
  assert.deepEqual([cardPw('0313').hint, cardPw('0313').maxLength, cardPw('0313').ok('12'), cardPw('0313').ok('1234')], [' 앞 2자리', 2, true, false])
  assert.equal(cardPw('0301').ok('12'), true)
  assert.deepEqual(['12', '1234', '123'].map(cardPw('0306').ok), [true, true, false])
})
