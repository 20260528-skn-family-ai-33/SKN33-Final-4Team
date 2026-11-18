// 계좌 연결 동의 — 서버 문서로만 동의받기 · 불러오기 실패 시 제출 막기 · 화면 버전 그대로 보내기 · 문서가 바뀌면 체크 초기화
// 11/18: 계좌(은행) · 카드 · 대출 화면을 나눴다 — 화면마다 그 범위 동의만, 카드 · 대출은 [연결하기]를 누른 뒤에만
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ConnectPage from './ConnectPage.jsx'
import ConnectCardPage from './ConnectCardPage.jsx'
import ConnectLoanPage from './ConnectLoanPage.jsx'
import SignupPage from './SignupPage.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { BANKS } from '../utils/institutions.js'

const { navigate, reload, sync } = vi.hoisted(() => ({ navigate: vi.fn(), reload: vi.fn(), sync: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn(), newRequestKey: () => 'consent-key' }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, useLocation: () => ({ state: null }) }))
vi.mock('../utils/financeSync.js', () => ({ runFinanceSync: sync }))
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
  sessionStorage.clear()
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
  await click(button('선택한 은행 연결하기'))
  expect(apiSend).not.toHaveBeenCalled()
  await click(button('다시 불러오기'))
  expect(reload).toHaveBeenCalled()
})

it('계좌 연결 화면은 은행 계좌 조회 동의만 보여 주고 그 버전만 보낸다 (회원가입 · 카드 · 대출 문서는 안 보임)', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  apiSend.mockResolvedValue({ items: [] })
  await render()
  expect(host.textContent).not.toContain('이용약관')
  expect(box('[선택] 카드')).toBeNull()
  expect(box('[선택] 대출')).toBeNull()
  await pickBank()
  await click(button('선택한 은행 연결하기'))
  expect(apiSend).not.toHaveBeenCalled()  // 은행 동의는 필수
  await click(box('[필수] 은행'))
  await click(button('선택한 은행 연결하기'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/consents/', 'POST', { consents: [
    { purpose: 'finance_inquiry', productScope: 'bank', documentVersion: 'v2' },
  ] }, { requestKey: 'consent-key' })
  expect(navigate).toHaveBeenCalledWith('/finance/connect/auth', { state: { banks: [BANKS.find((b) => b.popular).name], cards: [] } })
})

it('카드 연결은 [카드 연결하기]를 누른 사람에게만 카드사 · 카드 동의를 보여 주고, 건너뛰면 대출 정보 연결로', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  apiSend.mockResolvedValue({ items: [] })
  await act(() => root.render(<ConnectCardPage />))
  expect(box('[선택] 카드')).toBeNull()
  await click(button('건너뛰기'))
  expect(navigate).toHaveBeenCalledWith('/finance/connect/loan')
  expect(apiSend).not.toHaveBeenCalled()
  await click(button('카드 연결하기'))
  expect(box('[선택] 카드')).not.toBeNull()
  expect(box('[필수] 은행')).toBeNull()
  await click(button('신한카드'))
  await click(box('[선택] 카드'))
  await click(button('선택한 카드사 연결하기'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/consents/', 'POST', { consents: [
    { purpose: 'finance_inquiry', productScope: 'card', documentVersion: 'v2' },
  ] }, { requestKey: 'consent-key' })
  expect(navigate).toHaveBeenLastCalledWith('/finance/connect/auth', expect.objectContaining({ state: expect.objectContaining({ banks: [], kind: 'card' }) }))
})

it('대출 정보 연결은 누른 사람에게만 대출 동의를 보여 주고, 동의하면 은행만 다시 불러온 뒤 대출 확인으로', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  apiSend.mockResolvedValueOnce({}).mockResolvedValueOnce({ items: [
    { connectionId: 'c-bank', businessType: 'bank' }, { connectionId: 'c-card', businessType: 'card' },
  ] })
  sync.mockResolvedValue({ items: [{ connectionId: 'c-bank', status: 'succeeded', parts: [{ kind: 'loans', status: 'succeeded' }] }] })
  await act(() => root.render(<ConnectLoanPage />))
  expect(box('[선택] 대출')).toBeNull()
  await click(button('대출이 없어요'))
  expect(navigate).toHaveBeenCalledWith('/finance/connect/done')
  await click(button('대출 정보 연결하기'))
  await click(box('[선택] 대출'))
  await click(button('동의하고 불러오기'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/consents/', 'POST', { consents: [
    { purpose: 'finance_inquiry', productScope: 'loan', documentVersion: 'v2' },
  ] }, { requestKey: 'consent-key' })
  expect(sync).toHaveBeenCalledWith({ connectionIds: ['c-bank'] })
  expect(navigate).toHaveBeenLastCalledWith('/finance/loans', { replace: false, state: { from: 'onboarding' } })
})

it('은행 목록 실패와 일부 은행의 대출 조회 누락은 기존 오류 칸에서 재시도하고 모든 은행이 성공해야 이동한다', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  const banks = [{ connectionId: 'a', businessType: 'bank' }, { connectionId: 'b', businessType: 'bank', organizationName: '국민은행' }]
  const ok = (connectionId) => ({ connectionId, status: 'succeeded', parts: [{ kind: 'loans', status: 'succeeded' }] })
  apiSend.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error('은행 목록 조회 실패')).mockResolvedValue({ items: banks })
  sync.mockResolvedValueOnce({ items: [ok('a'), { connectionId: 'b', status: 'succeeded',
    parts: [{ kind: 'accounts', status: 'succeeded' }, { kind: 'loans', status: 'skipped' }] }] })
    .mockResolvedValueOnce({ items: [ok('a')] })
    .mockRejectedValueOnce(new Error('조회 시간 초과'))
    .mockResolvedValueOnce({ items: [ok('a'), ok('b')] })
  await act(() => root.render(<ConnectLoanPage />))
  await click(button('대출 정보 연결하기'))
  await click(box('[선택] 대출'))
  for (const message of ['은행 목록 조회 실패', '국민은행', '국민은행', '조회 시간 초과']) {
    await click(button('동의하고 불러오기'))
    expect(host.querySelector('[role=alert]').textContent).toContain(message)
    expect(navigate).not.toHaveBeenCalled()
  }
  await click(button('동의하고 불러오기'))
  expect(navigate).toHaveBeenCalledExactlyOnceWith('/finance/loans', { replace: false, state: { from: 'onboarding' } })
  expect(sync).toHaveBeenCalledTimes(4)
})

it('서버가 문서가 바뀌었다고 하면 다시 받고 체크를 지운다 · 새 버전에 이전 체크가 넘어가지 않는다', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  apiSend.mockRejectedValue(Object.assign(new Error('약관이 새로 바뀌었어요.'), { code: 'document_outdated', status: 409 }))
  await render()
  await pickBank()
  await click(box('[필수] 은행'))
  await click(button('선택한 은행 연결하기'))
  expect(reload).toHaveBeenCalled()
  expect(box('[필수] 은행').checked).toBe(false)
  expect(navigate).not.toHaveBeenCalled()

  await click(box('[필수] 은행'))
  useApi.mockReturnValue({ data: { items: [doc('bank', '[필수] 은행', 'v3')] }, error: null, reload })
  await render()
  expect(box('[필수] 은행').checked).toBe(false)  // v2 체크는 v3 에 넘어가지 않음
})

it('고를 때마다 이 탭에 남겨 두고, 버튼을 누르기 전에 나갔다 다시 들어와도 그대로 채운다 (11/12)', async () => {
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  await render()
  await pickBank()
  await click(box('[필수] 은행'))
  expect(navigate).not.toHaveBeenCalled()  // [선택한 은행 연결하기]는 누르지 않음

  await act(() => root.unmount())          // 내 정보로 갔다가 [저장하고 계속]으로 새로 들어온 것과 같음
  root = createRoot(host)
  await render()
  expect(button(BANKS.find((b) => b.popular).label).getAttribute('aria-pressed')).toBe('true')
  expect(box('[필수] 은행').checked).toBe(true)
})

it('30분이 지난 선택은 버리고 빈 화면부터 · 로그아웃하면 지운다', async () => {
  const { clearConnectDraft } = await import('../utils/connectDraft.js')
  useApi.mockReturnValue({ data: DOCS, error: null, reload })
  const bank = BANKS.find((b) => b.popular)
  sessionStorage.setItem('mc-connect-draft', JSON.stringify({ at: Date.now() - 31 * 60 * 1000, draft: { banks: [bank.name], agreed: [] } }))
  await render()
  expect(button(bank.label).getAttribute('aria-pressed')).toBe('false')
  clearConnectDraft()
  expect(sessionStorage.getItem('mc-connect-draft')).toBeNull()
})
