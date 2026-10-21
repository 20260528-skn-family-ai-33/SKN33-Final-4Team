// 저축 확인 (첫 설정 흐름: 고정지출 확인 다음, 예산 산정 방향 전)
// 내 계좌끼리 매달 같은 금액을 보낸 이체를 찾아 "저축인가요?"를 묻는다 (요구사항 TXN-005 · TXN-008)
//   저축이에요 → 저축 실적으로 세고, 그 월 합계를 지킬 돈의 '저축 최소 금액'(savings_floor)으로 넘긴다
//               추천 저축이 이보다 작으면 최소 금액까지 올리고 생활비에서 줄인다 (BUD-003, utils/direction.js)
//   아니에요   → 내 계좌끼리 옮긴 돈으로만 본다 (소비 아님 · 저축 아님)
// 저축은 사용자 확인 없이 자동으로 확정하지 않는다 (TXN-005)
// 목록은 시안 예시값 — 저축 후보 감지 API 가 생기면 바꾼다
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

// 감지 기준: 같은 받는 계좌 · 비슷한 날짜(±3일) · 같은 금액이 2~3달 이상 반복
const CANDIDATES = [
  { id: 'S1', to: '신한 자유적금 · 9012', from: '국민 · 5678', day: 25, amount: 300_000, count: 3 },
  { id: 'S2', to: '국민 주택청약 · 3456', from: '국민 · 5678', day: 10, amount: 200_000, count: 3 },
]
const ANSWERS = [['yes', '저축이에요'], ['no', '아니에요']]

export default function SavingsPage() {
  const navigate = useNavigate()
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
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>매달 반복되는 내 계좌끼리 이체를 찾지 못했어요. 저축 금액은 지킬 돈 설정에서 정할 수 있어요.</p></div>
      )}

      {CANDIDATES.map((c) => (
        <div className="mc-card" key={c.id}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{c.to}</strong><br /><span className="mc-muted">매월 {c.day}일 · {c.from}에서 보냄</span></span>
            <strong>{won(c.amount)}</strong>
          </div>
          <span className="mc-badge" style={{ marginTop: 8 }}>최근 3개월 {c.count}회 감지</span>
          <div className="mc-seg mc-seg-sm" role="group" aria-label={`${c.to} 이체가 저축인가요?`}>
            {ANSWERS.map(([value, label]) => (
              <button key={value} type="button" aria-pressed={answers[c.id] === value} onClick={() => pick(c.id, value)}>{label}</button>
            ))}
          </div>
        </div>
      ))}

      <div aria-live="polite" className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>매달 꼭 저축할 돈</span><strong>{won(savingsMin)}</strong></div>
        <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>지킬 돈 설정에서 저축을 이 금액보다 적게 정할 수 없어요.</p>
      </div>
      {error && <p className="mc-error" role="alert">{error}</p>}
      <p className="mc-note">'아니에요'를 고른 이체는 내 계좌끼리 옮긴 돈으로만 봐요. 소비로 세지 않아요.</p>
    </Layout>
  )
}
