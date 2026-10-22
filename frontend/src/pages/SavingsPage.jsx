// 저축 확인 · 정기 저축 관리 — 한 화면을 두 곳에서 쓴다 (docs/api/savings.md)
//   첫 설정 흐름: 고정지출 확인 다음, 예산 산정 방향 전. 찾은 후보마다 "저축인가요?"를 묻는다
//   마이 > 정기 저축 (location.state.from === 'my'): 확정된 저축 목록 + [수정] (10/22 PR #29 리뷰)
// 내 계좌끼리 매달 같은 금액을 보낸 이체를 찾아 "저축인가요?"를 묻는다 (요구사항 TXN-005 · TXN-008)
//   저축이에요 → 정기 저축으로 확정(recurring_saving). 확정된 것의 월 합계 = 저축 최소 금액 (서버가 계산, S-02)
//               추천 저축이 이보다 작으면 최소 금액까지 올리고 생활비에서 줄인다 (BUD-003, utils/direction.js)
//   아니에요   → 내 계좌끼리 옮긴 돈으로만 본다 (소비 아님 · 저축 아님)
// 저축은 사용자 확인 없이 자동으로 확정하지 않는다 (TXN-005)
// [+ 저축 직접 추가] = 자동으로 못 찾는 저축(다른 은행 적금 등). 바로 확정 (S-03)
// 찾은 후보의 [수정]은 이름 · 금액 · 이체일만 (계좌는 못 바꿈, S-11) → SavingPopup
// 목록은 시안 예시값 — 저축 API 가 생기면 GET /api/budgets/savings/ 로 바꾼다
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { usePopup } from '../popups/context.js'
import SavingPopup from '../popups/SavingPopup.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

// 감지 기준: 같은 받는 계좌 · 비슷한 날짜(±3일) · 같은 금액이 3달 연속 (S-05)
// to = 받는 계좌(연결 안 했으면 null), from = 보내는 계좌, count = 근거 이체 수(evidenceCount)
const CANDIDATES = [
  { id: 'S1', name: '신한 자유적금', to: '신한 자유적금 · ****9012', from: '국민 급여통장 · ****5678', day: 25, amount: 300_000, count: 3, status: 'suggested' },
  { id: 'S2', name: '주택청약', to: '국민 주택청약 · ****3456', from: '국민 급여통장 · ****5678', day: 10, amount: 200_000, count: 3, status: 'suggested' },
]
// 마이에서 열었을 때 — 확정된 저축 (manual = 직접 추가). 예: 주택청약은 [아니에요]를 고른 경우
const CONFIRMED = [
  { id: 'S1', name: '신한 자유적금', to: '신한 자유적금 · ****9012', from: '국민 급여통장 · ****5678', day: 25, amount: 300_000, status: 'confirmed' },
  { id: 'S3', name: '카카오 26주 적금', to: null, from: null, day: null, amount: 100_000, status: 'confirmed', manual: true },
]
const ANSWERS = [['yes', '저축이에요'], ['no', '아니에요']]

// "매월 25일 · 국민 급여통장 · ****5678 → 신한 자유적금 · ****9012"
function savingLine(s) {
  const day = s.day ? `매월 ${s.day}일` : '이체일 모름'
  const route = s.from || s.to ? `${s.from ?? '보내는 계좌 모름'} → ${s.to ?? '다른 은행'}` : '계좌 모름'
  return `${day} · ${route}`
}

// 마이 > 정기 저축
function ManageView() {
  const { openPopup } = usePopup()
  const total = CONFIRMED.reduce((sum, s) => sum + s.amount, 0)

  return (
    <Layout title="정기 저축" showBack>
      <div className="mc-eyebrow">정기 저축</div>
      <h1>매달 하는 저축</h1>
      <p className="mc-muted">여기 있는 저축은 예산에서 먼저 떼어 둬요. 금액을 바꾸거나 그만두면 다음 달 예산부터 반영돼요.</p>

      {CONFIRMED.length === 0 && (
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>등록된 정기 저축이 없어요. 매달 하는 저축이 있으면 추가해 주세요.</p></div>
      )}
      {CONFIRMED.map((s) => (
        <div className="mc-card" key={s.id}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{s.name}</strong><br /><span className="mc-muted">{savingLine(s)}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(s.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">{s.manual ? '직접 추가' : '자동으로 찾음'}</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<SavingPopup item={s} manage />)}>수정</button>
          </div>
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<SavingPopup manage />)}>+ 저축 직접 추가</button>

      <div className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>매달 꼭 저축할 돈</span><strong>{won(total)}</strong></div>
        <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>지킬 돈 설정에서 저축을 이 금액보다 적게 정할 수 없어요.</p>
      </div>
    </Layout>
  )
}

// 첫 설정 흐름 · 저축 확인
function OnboardingView() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const [answers, setAnswers] = useState({})   // { S1: 'yes' | 'no' }
  const [error, setError] = useState('')

  const savingsMin = CANDIDATES.filter((c) => answers[c.id] === 'yes').reduce((sum, c) => sum + c.amount, 0)
  const pick = (id, value) => { setAnswers({ ...answers, [id]: value }); setError('') }

  const onNext = () => {
    if (CANDIDATES.some((c) => !answers[c.id])) { setError('찾은 이체마다 저축인지 골라 주세요.'); return }
    navigate(PATHS.direction, { state: { savingsMin } })  // 저장은 API 연결 후
  }

  return (
    <Layout title="저축 확인" showBack foot={<button type="button" className="mc-primary" onClick={onNext}>확인하고 다음</button>}>
      <StepBar step={4} />
      <div className="mc-eyebrow">저축 확인</div>
      <h1>이 돈, 저축인가요?</h1>
      <p className="mc-muted">최근 3개월 동안 내 다른 계좌로 매달 같은 금액을 보낸 기록을 찾았어요. 저축이면 그 금액만큼은 꼭 저축하도록 예산을 짜요.</p>

      {CANDIDATES.length === 0 && (
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>매달 반복되는 내 계좌끼리 이체를 찾지 못했어요. 다른 은행 적금처럼 따로 하는 저축이 있으면 직접 추가해 주세요.</p></div>
      )}

      {CANDIDATES.map((c) => (
        <div className="mc-card" key={c.id}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{c.to ?? c.name}</strong><br /><span className="mc-muted">매월 {c.day}일 · {c.from}에서 보냄</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(c.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">최근 3개월 {c.count}회 감지</span>
            {/* 이름 · 금액 · 이체일만 고칠 수 있다 (S-11) */}
            <button type="button" className="mc-link" onClick={() => openPopup(<SavingPopup item={c} />)}>수정</button>
          </div>
          <div className="mc-seg mc-seg-sm" role="group" aria-label={`${c.to ?? c.name} 이체가 저축인가요?`}>
            {ANSWERS.map(([value, label]) => (
              <button key={value} type="button" aria-pressed={answers[c.id] === value} onClick={() => pick(c.id, value)}>{label}</button>
            ))}
          </div>
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<SavingPopup />)}>+ 저축 직접 추가</button>

      <div aria-live="polite" className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>매달 꼭 저축할 돈</span><strong>{won(savingsMin)}</strong></div>
        <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>지킬 돈 설정에서 저축을 이 금액보다 적게 정할 수 없어요.</p>
      </div>
      {error && <p className="mc-error" role="alert">{error}</p>}
      <p className="mc-note">'아니에요'를 고른 이체는 내 계좌끼리 옮긴 돈으로만 봐요. 소비로 세지 않아요. 계좌가 달라서 아니라면 [아니에요]를 누르고 직접 추가해 주세요.</p>
    </Layout>
  )
}

export default function SavingsPage() {
  const location = useLocation()
  return location.state?.from === 'my' ? <ManageView /> : <OnboardingView />
}
