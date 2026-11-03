// 실행: node --test src/utils/payOptions.test.js (frontend 폴더에서)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { payOptionsNote, payStatus } from './payOptions.js'

const err = (status, code) => Object.assign(new Error('x'), { status, code })

test('조회 상태를 실패 종류별로 나눈다 — 실패를 빈 목록(ready)으로 보지 않는다', () => {
  assert.equal(payStatus(null, null), 'loading')
  assert.equal(payStatus({ items: [] }, null), 'ready')
  assert.equal(payStatus(null, err(403, 'consent_required')), 'consent')
  assert.equal(payStatus(null, err(403, 'not_authenticated')), 'login')
  assert.equal(payStatus(null, err(500, 'server_error')), 'error')
  assert.equal(payStatus(null, Object.assign(new TypeError('Failed to fetch'))), 'error')  // 네트워크
  assert.equal(payStatus(null, err(500, 'server_error'), true), 'loading')  // 다시 받는 중
})

test('정상 빈 목록과 실패는 다른 문구', () => {
  assert.match(payOptionsNote('ready', 0, '카드'), /고를 수 있는 카드가 없어요/)
  assert.equal(payOptionsNote('ready', 2, '카드'), null)
  assert.match(payOptionsNote('error', 0, '계좌'), /불러오지 못했어요/)
  assert.match(payOptionsNote('consent', 0, '계좌'), /동의가 필요해요/)
  assert.match(payOptionsNote('login', 0, '계좌'), /로그인/)
  assert.match(payOptionsNote('loading', 0, '계좌'), /불러오는 중/)
})
