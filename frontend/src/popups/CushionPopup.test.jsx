// 쿠션 기준 확인 팝업 — 읽기 전용: 정상값·0원·null 표시, 편집칸·저장 없음, 확인으로 닫기
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CushionPopup from './CushionPopup.jsx'

const { closePopup } = vi.hoisted(() => ({ closePopup: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => {
  vi.clearAllMocks()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove() })
const show = (cushion) => act(() => root.render(<CushionPopup cushion={cushion} />))

it('정상값을 표시하고 편집칸·저장 버튼이 없다', () => {
  show({ goal: 8_400_000, monthly: 210_000, useRule: null })
  expect(host.textContent).toContain('8,400,000원')
  expect(host.textContent).toContain('210,000원')
  expect(host.textContent).toContain('월 실수령액의 3배')
  expect(host.textContent).toContain('아직 설정을 지원하지 않아요')
  expect(host.querySelector('input, select, form')).toBeNull()
  expect(host.textContent).not.toContain('저장')
})

it('0원과 null(미설정)을 구분한다', () => {
  show({ goal: null, monthly: 0, useRule: null })
  expect(host.textContent).toContain('급여 정보 확인이 필요해요')
  expect(host.textContent).toContain('0원')
  show({ goal: 0, monthly: null, useRule: null })
  expect(host.textContent).toContain('아직 정하지 않았어요')
  expect(host.textContent).not.toContain('급여 정보 확인이 필요해요')
})

it('확인 버튼으로 닫는다', () => {
  show({ goal: 1, monthly: 1, useRule: null })
  const ok = [...host.querySelectorAll('button')].find((b) => b.textContent === '확인')
  act(() => ok.click())
  expect(closePopup).toHaveBeenCalledTimes(1)
})
