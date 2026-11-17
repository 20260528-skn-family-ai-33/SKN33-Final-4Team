import assert from 'node:assert/strict'
import test from 'node:test'
import { salaryReviewContext, salaryReviewUrl } from './salaryReview.js'

test('페이지 새로고침 후에도 같은 대화와 월급 검토 상태를 복원한다', () => {
  const first = salaryReviewContext({ state: { salaryReview: true, budgetReturn: { conversationId: 'chat-1' } } })
  const url = salaryReviewUrl('/budgets/protect', first)
  assert.equal(url, '/budgets/protect?budgetConversation=chat-1&salaryReview=1')
  assert.deepEqual(salaryReviewContext({ search: new URL(url, 'http://localhost').search }), first)
})

test('복귀 주소는 외부 state 주소 대신 앱 채팅 주소로만 만든다', () => {
  const context = salaryReviewContext({ state: { budgetReturn: { conversationId: 'chat&other=1', returnTo: 'https://other.example/' } } })
  assert.equal(context.budgetReturn.returnTo, '/chat?budgetConversation=chat%26other%3D1')
  assert.equal(salaryReviewContext({ search: `?budgetConversation=${'a'.repeat(129)}` }).budgetReturn, null)
  assert.equal(salaryReviewContext({ state: { budgetReturn: { conversationId: {} } } }).budgetReturn, null)
})
