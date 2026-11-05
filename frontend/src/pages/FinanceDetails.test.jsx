// 실제 API 유틸·라우터·팝업을 함께 검증한다.
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PopupProvider from '../popups/PopupProvider.jsx'
import LoanCheckPage from './LoanCheckPage.jsx'
import ConnectionsPage from './ConnectionsPage.jsx'
import ConnectDonePage from './ConnectDonePage.jsx'
import HomePage from './HomePage.jsx'
import { PATHS } from '../routes.js'

const loan = {
  loanId: 'loan-1', lenderName: '합성은행', productName: '첫 대출', origin: 'provider',
  reportedPrincipal: 10000000, reportedBalance: 4000000, reportedRate: 0,
  manualBalance: null, manualRate: null, maturityDate: null, reportedPaymentDate: '2026-10-21',
  repaymentMethod: 'equal_principal', monthlyPayment: 120000, paymentDay: 25,
  paymentHistory: [{ date: '2026-10-21', principal: null, interest: 0 }],
  estimatedMonthlyPayment: null, overdue: null, fieldStates: {}, fetchedAt: null,
}
const bill = {
  billId: 'bill-1', issuerName: '합성카드', scopeType: 'issuer', billingMonth: '2026-11',
  totalAmount: 420000, confirmationStatus: 'estimated', fetchedAt: null,
  paymentDueDate: '2026-11-15', withdrawalDueDate: null, paymentAccount: null, cards: null,
  itemsState: 'partial', items: [{ billItemId: 'item-1', itemType: 'installment', merchantName: '합성가게',
    billedAmount: 60000, installmentMonths: 3, installmentRound: null, originalPurchaseAmount: null, purchasedOn: null, card: null }],
}
const home = {
  period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, budget: null,
  mustPay: [{ dueId: 'due-1', type: 'card_bill', sourceId: 'bill-1', name: '합성카드 청구서', dueDate: '2026-11-15', amount: 420000, paymentState: 'unpaid' }],
}
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
const failure = (code, message, fields) => reply({ error: { code, message, ...(fields ? { fields } : {}) } }, 400)
let host, root, loans, billData, homeData, loanError, billError, writes, fetchMock
function Location() { return <output data-path>{useLocation().pathname}</output> }
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.includes(text))
const click = (text) => act(async () => button(text).click())
const submit = (form) => act(async () => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
async function input(el, value) {
  await act(() => {
    Object.getOwnPropertyDescriptor(el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(el, value)
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}
async function mount(path = PATHS.loanCheck, state) {
  await act(() => root.render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <PopupProvider><Location /><Routes>
        <Route path={PATHS.loanCheck} element={<LoanCheckPage />} />
        <Route path={PATHS.connections} element={<ConnectionsPage />} />
        <Route path={PATHS.connectDone} element={<ConnectDonePage />} />
        <Route path={PATHS.home} element={<HomePage />} />
        <Route path="*" element={<p>이동한 화면</p>} />
      </Routes></PopupProvider>
    </MemoryRouter>,
  ))
}
const loanForm = () => host.querySelector('section.mc-loan form')
const dialogForm = () => host.querySelector('[role=dialog] form')
const sent = (method) => fetchMock.mock.calls.filter(([, options]) => options?.method === method)

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  loans = [structuredClone(loan)]
  billData = structuredClone(bill)
  homeData = structuredClone(home)
  loanError = billError = null
  writes = vi.fn(async (url, options) => {
    const body = JSON.parse(options.body)
    if (options.method === 'PATCH') {
      const row = loans.find((l) => url.includes(l.loanId))
      Object.assign(row, body)
      return reply(structuredClone(row))
    }
    const created = { ...loan, ...body, loanId: 'loan-new', origin: 'manual' }
    loans.push(created)
    return reply(created, 201)
  })
  fetchMock = vi.fn(async (url, options) => {
    if (options?.method) return writes(url, options)
    if (url === '/api/finance/loans/') return loanError ?? reply({ items: structuredClone(loans) })
    if (url === '/api/finance/card-bills/bill-1/') return billError ?? reply(billData)
    if (url === '/api/home/') return reply(homeData)
    if (url === '/api/csrf/') return reply({ ok: true })
    return reply({ items: [] })
  })
  vi.stubGlobal('fetch', fetchMock)
  document.cookie = 'csrftoken=test-token; path=/'
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

it('기관 원본·저장된 상환 정보를 표시하고 미확보 값은 0이나 연체 없음으로 바꾸지 않는다', async () => {
  await mount()
  expect(host.textContent).toContain('4,000,000원')
  expect(host.textContent).toContain('연 0%')
  expect(host.textContent).not.toContain('연체 없음')
  expect(host.textContent).not.toContain('KB국민은행')
  expect(host.querySelector('tbody').textContent).toContain('확인 필요')
  expect(loanForm().querySelector('input[type=text]').value).toBe('120,000')
  expect(loanForm().querySelectorAll('select')[0].value).toBe('equal_principal')
  expect(loanForm().querySelectorAll('select')[1].value).toBe('25')
})
it('보완 세 칸만 PATCH하고 지운 값은 null, 모름 선택은 unknown으로 저장한다', async () => {
  await mount()
  await input(loanForm().querySelector('input[type=text]'), '')
  await input(loanForm().querySelectorAll('select')[0], 'unknown')
  await input(loanForm().querySelectorAll('select')[1], '')
  expect(button('다음').disabled).toBe(true)
  await submit(loanForm())
  const [url, options] = sent('PATCH')[0]
  expect(url).toBe('/api/finance/loans/loan-1/')
  expect(JSON.parse(options.body)).toEqual({ monthlyPayment: null, repaymentMethod: 'unknown', paymentDay: null })
  expect(options.headers['X-CSRFToken']).toBe('test-token')
  expect(host.textContent).toContain('상환 정보를 저장했어요')
  expect(button('다음').disabled).toBe(false)
  await click('다음')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.salary)
})
it('수정 실패는 입력을 유지하고 재시도 성공 뒤 관리 화면으로 돌아간다', async () => {
  writes.mockResolvedValueOnce(failure('invalid', '입력값을 확인해 주세요.', { monthlyPayment: ['상환 금액 확인 필요'] }))
  await mount(PATHS.loanCheck, { from: 'manage' })
  await input(loanForm().querySelector('input[type=text]'), '230000')
  await submit(loanForm())
  expect(loanForm().querySelector('input[type=text]').value).toBe('230,000')
  expect(host.textContent).toContain('상환 금액 확인 필요')
  expect(button('확인 마침').disabled).toBe(true)
  await submit(loanForm())
  expect(sent('PATCH')).toHaveLength(2)
  expect(host.textContent).toContain('상환 정보를 저장했어요')
  await click('확인 마침')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.connections)
})
it('한 대출 저장으로 다른 대출의 수정 중 입력이 없어지지 않는다', async () => {
  loans.push({ ...loan, loanId: 'loan-2', productName: '두 번째 대출' })
  await mount()
  const forms = host.querySelectorAll('section.mc-loan form')
  await input(forms[0].querySelector('input[type=text]'), '200000')
  await input(forms[1].querySelector('input[type=text]'), '300000')
  await submit(forms[0])
  expect(forms[1].querySelector('input[type=text]').value).toBe('300,000')
  expect(button('다음').disabled).toBe(true)
  await submit(forms[1])
  expect(button('다음').disabled).toBe(false)
})
it.each([['', true], ['0', false], ['100000001', false]])('월 상환액 %s의 빈 값·범위를 검증한다', async (value, allowed) => {
  await mount()
  await input(loanForm().querySelector('input[type=text]'), value)
  await submit(loanForm())
  expect(sent('PATCH')).toHaveLength(allowed ? 1 : 0)
})
it('조회 실패·동의 없음은 빈 목록과 구분하고 재조회할 수 있다', async () => {
  loanError = failure('server_error', '잠시 후 다시 시도해 주세요.')
  await mount()
  expect(host.textContent).not.toContain('저장된 상환 중 대출이 없어요')
  loanError = failure('consent_required', '동의 필요')
  await click('다시 시도')
  expect(host.textContent).toContain('대출 조회 동의가 필요해요')
  expect(button('직접 등록')).toBeTruthy()
  await click('연결 화면에서 동의하기')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.connect)
})
it('대출 세션 만료는 로그인으로 이동한다', async () => {
  loanError = failure('not_authenticated', '로그인이 필요해요.')
  await mount()
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.login)
})
async function fillCreate() {
  await click('직접 등록')
  await input(host.querySelector('[name=lnLender]'), '합성재단')
  await input(host.querySelector('[name=lnProduct]'), '새 학자금')
  await input(host.querySelector('[name=lnBalance]').previousElementSibling, '3150000')
}
it('직접 등록 POST에 요청 번호·선택값 null을 보내고 성공 후 목록을 갱신한다', async () => {
  loans = []
  await mount(PATHS.connections)
  await fillCreate()
  await input(host.querySelector('[name=lnRate]'), '0')
  await submit(dialogForm())
  const [url, options] = sent('POST')[0]
  expect(url).toBe('/api/finance/loans/')
  expect(options.headers['Idempotency-Key']).toBeTruthy()
  expect(JSON.parse(options.body)).toEqual({ lenderName: '합성재단', productName: '새 학자금', manualBalance: 3150000, manualRate: 0, maturityDate: null, monthlyPayment: null, paymentDay: null, repaymentMethod: null })
  expect(host.querySelector('[role=dialog]')).toBeNull()
  expect(host.textContent).toContain('새 학자금')
  expect(host.textContent).toContain('3,150,000원')
  await click('새 학자금')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.loanCheck)
  expect(button('확인 마침')).toBeTruthy()
})
it('등록 오류는 입력을 유지하고 같은 폼 재시도는 같은 요청 번호를 사용한다', async () => {
  writes.mockResolvedValueOnce(failure('invalid', '입력값 확인', { maturityDate: ['만기일 확인 필요'] }))
  await mount()
  await fillCreate()
  await submit(dialogForm())
  expect(host.querySelector('[role=dialog]')).not.toBeNull()
  expect(host.textContent).toContain('만기일 확인 필요')
  expect(host.querySelector('[name=lnLender]').value).toBe('합성재단')
  await submit(dialogForm())
  expect(sent('POST')).toHaveLength(2)
  expect(sent('POST')[0][1].headers['Idempotency-Key']).toBe(sent('POST')[1][1].headers['Idempotency-Key'])
})
it('등록 응답을 기다리는 동안 중복 제출을 막고 성공 전에는 닫지 않는다', async () => {
  let finish
  writes.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  await mount()
  await fillCreate()
  const form = dialogForm()
  await act(() => {
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })
  expect(sent('POST')).toHaveLength(1)
  expect(dialogForm()).toBeTruthy()
  expect(button('등록 중').disabled).toBe(true)
  await act(() => finish(reply({ ...loan, loanId: 'new' }, 201)))
  expect(host.querySelector('[role=dialog]')).toBeNull()
})
async function openBill() {
  await mount(PATHS.home)
  await click('합성카드 청구서')
  await click('청구서 상세 보기')
}
it('홈 sourceId로 청구서를 열고 일부 내역 합계를 총액으로 대체하지 않는다', async () => {
  await openBill()
  expect(fetchMock).toHaveBeenCalledWith('/api/finance/card-bills/bill-1/', expect.any(Object))
  const popup = host.querySelector('[role=dialog]')
  expect(popup.getAttribute('aria-label')).toBe('카드 청구서 상세')
  for (const text of ['420,000원', '60,000원', '일부만 조회', '목록을 확인할 수 없어요', '회차 확인 필요']) expect(popup.textContent).toContain(text)
  for (const text of ['0장', '전체 0원', '신한카드']) expect(popup.textContent).not.toContain(text)
})
it.each([['complete', [], '구매 내역이 없어요.'], ['unavailable', null, '구매 내역을 확보하지 못했어요.']])('청구 상세 %s를 구분한다', async (itemsState, items, message) => {
  billData = { ...billData, itemsState, items, totalAmount: null }
  await openBill()
  expect(host.querySelector('[role=dialog]').textContent).toContain(message)
  expect(host.querySelector('[role=dialog]').textContent).toContain('금액 확인 필요')
})
it('청구서 실패 뒤 재조회하고 카드 동의가 없으면 연결 화면으로 안내한다', async () => {
  billError = failure('not_found', '찾을 수 없어요.')
  await openBill()
  expect(host.textContent).toContain('청구서를 찾을 수 없어요')
  billError = failure('consent_required', '동의 필요')
  await click('다시 시도')
  expect(host.textContent).toContain('카드 조회 동의가 필요해요')
  await click('연결 화면에서 동의하기')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.connect)
})
it('홈 대출 항목에서 해당 대출을 우선 표시하고 저장 후 홈으로 돌아간다', async () => {
  loans.push({ ...loan, loanId: 'loan-2', productName: '선택한 대출' })
  homeData.mustPay = [{ ...homeData.mustPay[0], type: 'loan', sourceId: 'loan-2', name: '선택한 대출 상환' }]
  await mount(PATHS.home)
  await click('선택한 대출 상환')
  await click('대출 상환 정보 확인')
  expect(host.querySelector('section.mc-loan h2').textContent).toContain('선택한 대출')
  await submit(loanForm())
  expect(sent('PATCH')[0][0]).toBe('/api/finance/loans/loan-2/')
  await click('확인 마침')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.home)
})
it('연결 완료에서 대출 확인을 선택하고 이후 월급 입력으로 진행한다', async () => {
  loans = []
  await mount(PATHS.connectDone)
  await click('대출 상환 정보 확인·등록')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.loanCheck)
  expect(host.textContent).toContain('저장된 상환 중 대출이 없어요')
  await click('다음')
  expect(host.querySelector('[data-path]').textContent).toBe(PATHS.salary)
})

// 수동 등록은 조회 동의를 요구하지 않는 서버 계약을 따른다.
it('조회 동의 없이도 직접 등록할 수 있고 목록 조회 제한을 알린다', async () => {
  loanError = failure('consent_required', '동의 필요')
  await mount(PATHS.connections)
  await fillCreate()
  await submit(dialogForm())
  expect(sent('POST')).toHaveLength(1)
  expect(host.textContent).toContain('새 학자금 대출을 등록했어요')
  expect(host.textContent).toContain('목록 조회는 대출 조회 동의 후')
  expect(fetchMock.mock.calls.some(([url]) => url === '/api/finance/consents/')).toBe(false)
})

it('등록 요청 뒤 닫은 팝업의 늦은 응답은 새로 연 팝업을 닫지 않는다', async () => {
  let finish
  writes.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  await mount()
  await fillCreate()
  await submit(dialogForm())
  await click('닫기 ×')
  await click('직접 등록')
  const newForm = dialogForm()
  await act(() => finish(reply({ ...loan, loanId: 'new' }, 201)))
  expect(dialogForm()).toBe(newForm)
})
