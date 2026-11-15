import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import RegionSelect from './RegionSelect.jsx'
import { cities, districts } from '../utils/policyCards.js'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const items = [
  { code: '41000', name: '경기도', regions: [{ code: '41111', name: '수원시 장안구' }, { code: '41113', name: '수원시 권선구' }, { code: '41190', name: '부천시' }] },
  { code: '36110', name: '세종특별자치시', regions: [{ code: '36110', name: '세종특별자치시' }] },
  { code: '28000', name: '인천광역시', regions: [{ code: '28185', name: '연수구' }, { code: '28710', name: '강화군' }] },
]
let host, root, sent
beforeEach(() => {
  sent = []
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ items }) })))
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })

const render = (done = false) => act(async () => root.render(<RegionSelect done={done} onSubmit={(text) => sent.push(text)} />))
const select = (label) => host.querySelector(`select[aria-label="${label}"]`)
const choose = (label, value) => act(async () => {
  const el = select(label)
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, value)
  el.dispatchEvent(new Event('change', { bubbles: true }))
})
const submit = () => act(async () => host.querySelector('button[type="submit"]').click())

it('시·군·구 칸은 시 이름만 한 번씩, 구 칸은 고른 시의 구만. 세종처럼 시도 자신뿐이면 비운다', () => {
  expect(cities(items[0])).toEqual(['수원시', '부천시'])
  expect(districts(items[0], '수원시')).toEqual(['장안구', '권선구'])
  expect(districts(items[0], '부천시')).toEqual([])
  expect(cities(items[1])).toEqual([])
})

it('도 · 시 · 구 어느 칸까지 골라도 고른 데까지의 전체 이름을 문장으로 보낸다', async () => {
  await render()
  expect(host.querySelector('button[type="submit"]').disabled).toBe(true)  // 시도를 고르기 전
  await choose('시·도', '전국'); await submit()
  expect(select('시·군·구')).toBeNull()  // 전국은 아래 칸이 없다
  await choose('시·도', '경기도'); await submit()
  await choose('시·군·구', '부천시'); await submit()
  expect(select('구').disabled).toBe(true)  // 구가 없는 시
  await choose('시·군·구', '수원시'); await submit()
  await choose('구', '장안구'); await submit()
  expect(sent).toEqual(['전국', '경기도', '경기도 부천시', '경기도 수원시', '경기도 수원시 장안구'])
})

it('고른 시도에 없는 단계의 칸은 뺀다 (인천 2칸 · 세종 1칸 · 경기 3칸)', async () => {
  await render()
  const count = () => host.querySelectorAll('select').length
  expect(count()).toBe(3)
  await choose('시·도', '인천광역시')
  expect(count()).toBe(2)
  await choose('시·군·구', '연수구'); await submit()
  await choose('시·도', '세종특별자치시')
  expect(count()).toBe(1)
  await choose('시·도', '경기도')
  expect(count()).toBe(3)
  expect(sent).toEqual(['인천광역시 연수구'])
})

it('지난 질문이면 잠근다', async () => {
  await render(true)
  expect(host.querySelector('fieldset').disabled).toBe(true)
})
