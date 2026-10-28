import assert from 'node:assert/strict'
import test from 'node:test'
import { applySavingsMin, recommendFor } from './direction.js'

test('자취: 저축 133만원을 떼면 55만 / 5만 / 10만원으로 재배분한다', () => {
  const rec = applySavingsMin(recommendFor('alone'), 1_330_000)
  assert.equal(rec.livingBudget, 550_000)
  assert.equal(rec.spare, 50_000)
  assert.equal(rec.cushionMonthly, 100_000)
})

test('본가: 남은 60만원은 45만 / 5만 / 10만원으로 재배분한다', () => {
  const rec = applySavingsMin(recommendFor('family'), 1_430_000)
  assert.equal(rec.livingBudget, 450_000)
  assert.equal(rec.spare, 50_000)
  assert.equal(rec.cushionMonthly, 100_000)
})

for (const housing of ['alone', 'family']) {
  test(`${housing}: 추천 저축 이하이면 기존 추천을 유지한다`, () => {
    const rec = recommendFor(housing)
    for (const minimum of [0, rec.savingFloor]) {
      assert.deepEqual(applySavingsMin(rec, minimum), {
        ...rec, savingsMin: minimum, rebalanced: false, blocked: false, deficit: 0,
      })
    }
  })

  test(`${housing}: 재배분 후 합계와 1만원 내림을 유지한다`, () => {
    const base = recommendFor(housing)
    for (const minimum of [base.savingFloor + 1, 1_333_333, base.available - 1]) {
      const rec = applySavingsMin(base, minimum)
      assert.equal(rec.savingFloor + rec.livingBudget + rec.spare + rec.cushionMonthly, base.available)
      assert.equal(rec.livingBudget % 10_000, 0)
      assert.equal(rec.cushionMonthly % 10_000, 0)
      assert.ok(rec.spare >= 0)
      assert.equal(rec.blocked, false)
    }
  })
}

test('저축이 배분 가능액 이상이면 재배분을 막고 부족액을 계산한다', () => {
  const base = recommendFor('alone')
  for (const deficit of [0, 10_000]) {
    const rec = applySavingsMin(base, base.available + deficit)
    assert.equal(rec.blocked, true)
    assert.ok(rec.deficit === deficit)
    assert.equal(rec.livingBudget + rec.spare + rec.cushionMonthly, 0)
  }
})
