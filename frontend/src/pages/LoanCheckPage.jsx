// 대출 확인
// 대출 목록은 시안 예시값 — CODEF 대출 조회 API 가 생기면 바꾼다
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import { MONTH_DAYS } from '../utils/days.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import LoanAddPopup from '../popups/LoanAddPopup.jsx'

const METHODS = ['원리금균등', '원금균등', '만기일시', '잘 모르겠어요']
// tag: auto = CODEF 조회됨 / plan = 직접 입력 / warn = 확인 필요
const LOANS = [
  {
    id: 'L1', name: '신용대출', org: 'KB국민은행', source: ['auto', 'CODEF 조회'], asOf: '오늘 09:30 기준',
    info: [['약정금액', 'auto', '조회됨', '10,000,000원'], ['잔액', 'auto', '조회됨', '6,420,000원'], ['금리', 'auto', '조회됨', '연 5.8%'], ['만기일', 'auto', '조회됨', '2028.03.25']],
    overdue: false,  // 연체 여부 (CODEF 대출 거래내역 resState)
    // 지난 납부: 원금(resPrincipal) + 이자(resInterest) = 낸 돈(resTranAmount)
    history: [
      { date: '07.25', principal: 285_000, interest: 27_000 },
      { date: '08.25', principal: 285_000, interest: 25_500 },
      { date: '09.25', principal: 285_000, interest: 24_000 },
    ],
    estimate: 310_500, estimateNote: '추정 310,500원 · 확인된 납부 3건(07~09월) 평균',
    day: '25', dayNote: '최근 납부일 09.25 · 기관에 따라 이자 납입일일 수 있어요',
  },
  {
    id: 'L2', name: '학자금대출', org: '한국장학재단', source: ['plan', '직접 등록한 대출'],
    info: [['약정금액', 'warn', '확인 필요', '-'], ['잔액', 'plan', '직접 입력', '3,150,000원'], ['금리', 'plan', '직접 입력', '연 1.7%'], ['만기일', 'warn', '확인 필요', '-']],
    history: '불러온 납부 내역이 없어요.',
    day: '', dayNote: '납부일을 직접 골라 주세요',
  },
]

function LoanCard({ loan }) {
  const [method, setMethod] = useState(null)
  const [amount, setAmount] = useState('')

  return (
    <div className="mc-card mc-loan">
      <div className="mc-row">
        <h2>{loan.name} <span className="mc-hint">{loan.org}</span></h2>
        <span className={`mc-loan-tag ${loan.source[0]}`}>{loan.source[1]}</span>
      </div>
      {loan.overdue === true && <p className="mc-loan-overdue" role="note">연체 중인 대출이에요. 연체가 길어지면 신용점수가 떨어질 수 있어요. 은행에 먼저 확인해 주세요.</p>}
      {loan.overdue === false && <p className="mc-loan-ok">연체 없음 · 조회됨</p>}
      <h3 className="mc-loan-h">조회된 대출 정보 {loan.asOf && <small>{loan.asOf}</small>}</h3>
      <div className="mc-loan-grid">
        {loan.info.map(([label, tag, tagText, value]) => (
          <div key={label}><span>{label} <em className={`mc-loan-tag ${tag}`}>{tagText}</em></span><strong>{value}</strong></div>
        ))}
      </div>
      <h3 className="mc-loan-h">지난 납부 내역</h3>
      {Array.isArray(loan.history) ? (
        <table className="mc-loan-table">
          <thead><tr><th>납부일</th><th>원금</th><th>이자</th><th>낸 돈</th></tr></thead>
          <tbody>
            {loan.history.map((h) => (
              <tr key={h.date}><td>{h.date}</td><td>{won(h.principal)}</td><td>{won(h.interest)}</td><td><b>{won(h.principal + h.interest)}</b></td></tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="mc-loan-hist">{loan.history}</p>
      )}
      <div className="mc-loan-in">
        <h3>앞으로 낼 돈 <span className="mc-hint">직접 확인</span></h3>
        <p className="mc-loan-lab">상환 방식</p>
        <div className="mc-chipbar">
          {METHODS.map((m) => <button key={m} type="button" aria-pressed={method === m} onClick={() => setMethod(m)}>{m}</button>)}
        </div>
        <div className="mc-loan-2">
          <label className="mc-field">
            <span className="mc-loan-lab">월 상환 예정액 {loan.estimate && (
              // 지난 납부 평균을 칸에 채워 넣기
              <button type="button" className="mc-loan-link" onClick={() => setAmount(String(loan.estimate))}>추정액 쓰기</button>
            )}</span>
            <MoneyInput placeholder={loan.estimate ? won(loan.estimate).replace('원', '') : '금액 입력'}
              value={amount} onChange={(digits) => setAmount(digits)} />
            {loan.estimateNote && <small className="mc-loan-sub">{loan.estimateNote}</small>}
          </label>
          <label className="mc-field">
            <span className="mc-loan-lab">다음 납부 예정일</span>
            <select defaultValue={loan.day}>
              <option value="">선택</option>
              {MONTH_DAYS.map((d) => <option key={d} value={String(d)}>매월 {d}일</option>)}
            </select>
            <small className="mc-loan-sub">{loan.dayNote}</small>
          </label>
        </div>
      </div>
    </div>
  )
}

export default function LoanCheckPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  // 마이 > 연결 관리 · 홈 '꼭 나갈 돈'에서 열면 첫 설정 흐름이 아니라 확인 · 수정만 하고 돌아간다
  const manage = ['my', 'home'].includes(useLocation().state?.from)
  const next = () => (manage ? navigate(-1) : navigate(PATHS.salary))  // 저장은 API 연결 후 (PATCH /api/finance/loans/<id>/)

  return (
    <Layout
      title="대출 확인"
      showBack
      foot={<>
        {!manage && <button type="button" onClick={next}>나중에 입력</button>}
        <button type="button" className="mc-primary" onClick={next}>상환 예정액 저장</button>
      </>}
    >
      {!manage && <StepBar step={2} />}
      <div className="mc-eyebrow">대출 확인</div>
      <h1>{manage ? '상환 중인 대출' : '불러온 대출을 확인해 주세요'}</h1>
      <p className="mc-muted">불러온 대출을 확인하고, 앞으로 낼 금액을 알려 주세요.</p>
      {LOANS.map((loan) => <LoanCard key={loan.id} loan={loan} />)}
      {/* 직접 등록 (API 35, 결정 Y) — 학자금 · 캐피탈처럼 은행 연결로 안 잡히는 대출 */}
      <button type="button" className="mc-link" onClick={() => openPopup(<LoanAddPopup />)}>+ 조회되지 않은 대출 직접 등록</button>
      <p className="mc-note">여기서는 상환 예정액만 저장해요. 예산에 넣을 때는 금액과 출처를 보여 드리고 승인 후 반영해요.</p>
      <div className="mc-error" role="alert"></div>
    </Layout>
  )
}
