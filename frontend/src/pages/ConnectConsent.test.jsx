// 계좌 연결 동의 — 서버 문서로만 동의받기 · 불러오기 실패 시 제출 막기 · 화면 버전 그대로 보내기 · 문서가 바뀌면 체크 초기화
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ConnectPage from './ConnectPage.jsx'
import SignupPage from './SignupPage.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { BANKS } from '../utils/institutions.js'

const { navigate, reload } = vi.hoisted(() => ({ navigate: vi.fn(), reload: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn(), newRequestKey: () => 'consent-key' }))
vi.mock('react-router', () => ({ useNavigate: () => navigate }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <div>{children}{foot}</div> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))

const doc = (scope, label, version = 'v2') => ({
  purpose: 'finance_inquiry', productScope: scope, version, documentHash: `hash-${scope}-${version}`,
  document: { label, title: label, version, date: '2026.10.20', required: scope === 'bank', sections: [] },
})
const DOCS = { items: [
  { purpose: 'terms', productScope: null, version: 'v1', documentHash: 'h', document: { label: '이용약관', sections: [] } },
  { purpose: 'budget_ai', productScope: null, version: 'v1', documentHash: 'ai', document: { label: '예산 AI 선택 동의', required: false, sections: [] } },
  doc('bank', '[필수] 은행'), doc('card', '[선택] 카드'), doc('loan', '[선택] 대출'),
] }

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => {
  vi.clearAllMocks()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.includes(text))
const box = (label) => host.querySelector(`input[aria-label="${label}"]`)
const click = (el) => act(async () => el.click())
const render = () => act(() => root.render(<ConnectPage />))
const pickBank = () => click(button(BANKS.find((b) => b.popular).label))

it('예산 AI 선택 문서가 회원가입 필수 동의에 섞이지 않는다', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  await act(() => root.render(<SignupPage />))
  expect(host.textContent).toContain('이용약관')
  expect(host.textContent).not.toContain('예산 AI 선택 동의')
})

it('문서를 못 불러오면 동의 칸이 없고 제출해도 저장하지 않는다 · 다시 불러오기', async () => {
  useApi.mockReturnValue({ data: null, error: new Error('x'), reload })
  await render()
  expect(host.textContent).toContain('동의 문서를 불러오지 못했어요')
  expect(box('[필수] 은행')).toBeNull()
  await pickBank()
  await click(button('선택한 기관 연결하기'))
  expect(apiSend).not.toHaveBeenCalled()
  await click(button('다시 불러오기'))
  expect(reload).toHaveBeenCalled()
})

it('화면에 보여 준 금융 문서 버전만 보낸다 (회원가입 문서는 안 보임)', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  apiSend.mockResolvedValue({ items: [] })
  await render()
  expect(host.textContent).not.toContain('이용약관')
  await pickBank()
  await click(box('[필수] 은행'))
  await click(box('[선택] 대출'))
  await click(button('선택한 기관 연결하기'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/consents/', 'POST', { consents: [
    { purpose: 'finance_inquiry', productScope: 'bank', documentVersion: 'v2' },
    { purpose: 'finance_inquiry', productScope: 'loan', documentVersion: 'v2' },
  ] }, { requestKey: 'consent-key' })
  expect(navigate).toHaveBeenCalled()
})

it('서버가 문서가 바뀌었다고 하면 다시 받고 체크를 지운다 · 새 버전에 이전 체크가 넘어가지 않는다', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  apiSend.mockRejectedValue(Object.assign(new Error('약관이 새로 바뀌었어요.'), { code: 'document_outdated', status: 409 }))
  await render()
  await pickBank()
  await click(box('[필수] 은행'))
  await click(button('선택한 기관 연결하기'))
  expect(reload).toHaveBeenCalled()
  expect(box('[필수] 은행').checked).toBe(false)
  expect(navigate).not.toHaveBeenCalled()

  await click(box('[필수] 은행'))
  useApi.mockReturnValue({ data: { items: [doc('bank', '[필수] 은행', 'v3')] }, error: null, reload })
  await render()
  expect(box('[필수] 은행').checked).toBe(false)  // v2 체크는 v3 에 넘어가지 않음
})
