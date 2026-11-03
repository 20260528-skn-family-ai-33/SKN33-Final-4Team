import assert from 'node:assert/strict'
import test from 'node:test'
import { PATHS, afterAccountPick } from './routes.js'

test('연결 관리에서 계좌 선택을 바꾸면 카드 선택을 거치지 않고 연결 관리로 돌아간다', () => {
  assert.equal(afterAccountPick({ from: 'manage' }), PATHS.connections)
})

test('가입 과정: 카드사를 연결했으면 카드 선택, 아니면 연결 결과', () => {
  assert.equal(afterAccountPick({ cards: true }), PATHS.connectCards)
  assert.equal(afterAccountPick({ cards: false }), PATHS.connectDone)
  assert.equal(afterAccountPick(null), PATHS.connectCards)
})
