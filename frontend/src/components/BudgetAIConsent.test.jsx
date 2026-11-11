import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import BudgetAIConsent from './BudgetAIConsent.jsx'
import { apiSend } from '../utils/api.js'

const { openPopup } = vi.hoisted(() => ({ openPopup: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn() }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup }) }))
const URL = '/api/budgets/ai-consent/'
const state = (status = 'not_granted') => ({ available: true, status, documentVersion: 'v3', documentHash: 'synthetic-hash',
  document: { title: '예산 AI 전송 안내', required: false, version: 'v3', date: '2026.11.10', sections: [] },
  healthDocumentVersion: 'v1', healthDocumentHash: 'synthetic-health-hash',
  healthDocument: { title: '의료·건강 별도 동의', required: false, version: 'v1', sections: [] } })
let host, root, toastBox
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  vi.clearAllMocks()
  apiSend.mockResolvedValue(state())
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  toastBox = document.createElement('div'); toastBox.id = 'mc-toast'; document.body.append(toastBox)  // 실제 화면에서는 Layout 이 갖고 있다
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); toastBox.remove() })
const render = () => act(() => root.render(<BudgetAIConsent />))
const button = (text) => [...host.querySelectorAll('button')].find(b => b.textContent === text || b.getAttribute('aria-label') === text)
const click = (text) => act(() => button(text).click())
const check = () => act(() => { for (const input of host.querySelectorAll('input')) input.click() })

it.each([false, undefined])('available=%s이면 새 동의 UI를 숨긴다', async (available) => {
  apiSend.mockResolvedValueOnce({ ...state(), available })
  await render()
  expect(host.textContent).toBe('')
  expect(apiSend.mock.calls).toEqual([[URL, 'GET']])
})

it('화면을 연 뒤 기능이 꺼져 저장이 거부되면 새 상태를 읽고 동의 UI를 숨긴다', async () => {
  await render(); await check()
  apiSend.mockRejectedValueOnce({ code: 'budget_ai_unavailable' })
    .mockResolvedValueOnce({ ...state(), available: false })
  await click('동의 저장')
  expect(host.textContent).toBe('')
  expect(apiSend.mock.calls.filter(([, method]) => method === 'PUT')).toHaveLength(1)
})

it('비활성 상태에서도 기존 동의의 철회는 제공하고 새 동의는 받지 않는다', async () => {
  apiSend.mockResolvedValueOnce({ ...state('needs_renewal'), available: false })
  await render()
  expect(host.querySelectorAll('input')).toHaveLength(0)
  expect(host.textContent).toContain('현재 중단')
  expect(button('동의 저장')).toBeUndefined()
  apiSend.mockResolvedValueOnce({ ...state('revoked'), available: false })
  await click('예산 AI 전송 동의 철회'); await click('철회하기')
  expect(apiSend).toHaveBeenLastCalledWith(URL, 'DELETE', { confirm: true })
  expect(host.textContent).toBe('')
})

it('선택 동의는 기본 해제이고 조회만 하며 기본 추천 이용 가능을 안내한다', async () => {
  await render()
  expect(host.querySelector('input').checked).toBe(false)
  expect(button('동의 저장').disabled).toBe(true)
  expect(apiSend.mock.calls).toEqual([[URL, 'GET']])
  expect(host.textContent).toContain('기본 예산 추천을 이용할 수 있어요')
})

it('체크 후 저장해야 화면의 정확한 문서 버전·해시를 서버에 전송한다', async () => {
  await render(); await check()
  expect(apiSend).toHaveBeenCalledTimes(1)
  apiSend.mockResolvedValueOnce(state('granted'))
  await click('동의 저장')
  expect(apiSend).toHaveBeenLastCalledWith(URL, 'PUT', { agreed: true, documentVersion: 'v3', documentHash: 'synthetic-hash',
    healthAgreed: true, healthDocumentVersion: 'v1', healthDocumentHash: 'synthetic-health-hash' })
  expect(toastBox.textContent).toBe('AI 전송에 동의했어요')  // 잠깐 떴다 사라지는 안내
  expect(host.textContent).toContain('동의함')
  expect(host.textContent).not.toContain('동의했어요')
  expect(apiSend.mock.calls.some(([url]) => url.includes('/recommend/'))).toBe(false)
})

it('문서 팝업 동의는 체크만 하며 서버 저장을 자동으로 실행하지 않는다', async () => {
  await render(); await click('전송 항목·보관 안내 보기')
  const popup = openPopup.mock.calls[0][0]
  expect(popup.props.doc).toEqual(state().document)
  await act(() => popup.props.onAgree())
  expect(host.querySelector('input').checked).toBe(true)
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(button('동의 저장').disabled).toBe(true)
})

it('국외이전과 건강 요약에 각각 체크해야 저장할 수 있다', async () => {
  await render()
  await act(() => host.querySelector('input').click())
  expect(button('동의 저장').disabled).toBe(true)
  await click('건강 관련 요약 처리 안내 보기')
  const popup = openPopup.mock.calls[0][0]
  expect(popup.props.doc).toEqual(state().healthDocument)
  await act(() => popup.props.onAgree())
  expect(button('동의 저장').disabled).toBe(false)
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('철회는 안내를 본 뒤 [철회하기]를 한 번 더 눌러야 하고 기존 예산·금융자료를 삭제한다고 안내하지 않는다', async () => {
  apiSend.mockResolvedValueOnce(state('granted'))
  await render()
  expect(host.textContent).not.toContain('그대로 유지해요')
  await click('예산 AI 전송 동의 철회')
  expect(host.textContent).toContain('철회해도 승인한 예산과 금융자료는 그대로 유지해요')
  expect(apiSend.mock.calls).toEqual([[URL, 'GET']])  // 첫 번째 누름은 안내만
  await click('취소')
  expect(button('철회하기')).toBeUndefined()
  expect(apiSend.mock.calls).toEqual([[URL, 'GET']])
  apiSend.mockResolvedValueOnce(state('revoked'))
  await click('예산 AI 전송 동의 철회'); await click('철회하기')
  expect(apiSend).toHaveBeenLastCalledWith(URL, 'DELETE', { confirm: true })
  expect(toastBox.textContent).toBe('예산 AI 전송 동의를 철회했어요')
  expect(host.textContent).toContain('철회함')
  expect(host.querySelector('input').checked).toBe(false)
})

it('동의 문서가 바뀌면 새 문서를 읽고 체크를 지운다', async () => {
  await render(); await check()
  apiSend.mockRejectedValueOnce({ code: 'document_outdated' })
    .mockResolvedValueOnce({ ...state('needs_renewal'), documentVersion: 'v2', documentHash: 'synthetic-v2' })
  await click('동의 저장')
  expect(host.textContent).toContain('다시 확인해 주세요')
  expect(host.querySelector('input').checked).toBe(false)
  expect(button('동의 저장').disabled).toBe(true)
})

it('동의 조회 실패를 동의 완료로 표시하지 않고 재시도한다', async () => {
  apiSend.mockRejectedValueOnce(new Error('synthetic'))
  await render()
  expect(host.querySelector('input')).toBeNull()
  expect(host.textContent).toContain('기본 예산 추천은 이용할 수 있어요')
  await click('동의 상태 다시 확인')
  expect(host.querySelector('input').checked).toBe(false)
})

it('처리 중 두 번 눌러도 한 번만 저장한다', async () => {
  await render(); await check()
  let resolve
  apiSend.mockImplementationOnce(() => new Promise(r => { resolve = r }))
  await act(() => { button('동의 저장').click(); button('동의 저장').click() })
  expect(apiSend.mock.calls.filter(([, method]) => method === 'PUT')).toHaveLength(1)
  expect(button('동의 저장').disabled).toBe(true)
  await act(() => resolve(state('granted')))
})
