// 가운데 창: 받은 월급이 등록한 실수령액과 다를 때 어떻게 할지 고르기 (11/13, docs/api/salary.md '월급 차이 안내')
// props: gap = period.salaryGap { diff, received, registered, basis }, onDone = 답한 뒤 홈 다시 불러오기
// 선택지는 둘 (11/13 정리): [이번 예산 다시 짜기] = 이번 주기만 받은 금액으로 나누게 저장(apply)하고 지킬 돈 화면을 새 추천값으로 연다
//   (늘어난 · 줄어든 돈을 어디에 둘지는 지킬 돈 화면에서 정한다) · [그대로 두기] = 기준은 그대로(keep)
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { apiSend } from '../utils/api.js'
import { won } from '../utils/money.js'
import { toast } from '../utils/toast.js'
import { PATHS } from '../routes.js'

export default function SalaryGapPopup({ gap, onDone }) {
  const navigate = useNavigate()
  const { closePopup } = usePopup()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const less = gap.diff < 0
  const amount = won(Math.abs(gap.diff))

  const choices = [
    ['apply', '이번 예산 다시 짜기', '이번 주기만 받은 금액으로 지킬 돈과 예산을 다시 정해요'],
    ['keep', '그대로 두기', '등록한 실수령액 기준으로 계속 써요'],
  ]

  const pick = async (answer) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/budgets/salary/gap/', 'POST', { answer, salaryAmount: gap.received })
    } catch (err) {
      setBusy(false)
      setError(err.message)
      if (err.fields?.salaryAmount) onDone?.()  // 받은 월급이 바뀌었으면 홈을 새로 불러온다
      return
    }
    closePopup()
    if (answer === 'apply') {
      // 받은 금액으로 다시 나눈 추천값부터 보여 준다 (기존 금액 대비 증감은 칸 아래 '기존보다'로 보임)
      navigate(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })
    } else {
      toast('지금 예산 그대로 둘게요')
      onDone?.()
    }
  }

  return (
    <Popup label="월급 차이 확인" dialog>
      <h2>월급이 {amount} {less ? '덜' : '더'} 들어왔어요</h2>
      {/* 11/13: 두 금액을 글로 늘어놓지 않고 영수증처럼 줄 맞춰 비교 + 차이 */}
      <dl className="mc-compare">
        <div><dt>등록한 월급</dt><dd>{won(gap.registered)}</dd></div>
        <div><dt>이번에 받은 월급</dt><dd>{won(gap.received)}</dd></div>
        <div className="mc-compare-total"><dt>차이</dt><dd className={less ? 'is-less' : 'is-more'}>{less ? '−' : '+'}{amount}</dd></div>
      </dl>
      <div className="mc-apply-choices">
        {choices.map(([k, label, sub]) => (
          <button key={k} type="button" className="mc-choice" disabled={busy} onClick={() => pick(k)}>
            <strong>{label}</strong><span className="mc-muted">{sub}</span>
          </button>
        ))}
      </div>
      {error && <p className="mc-error" role="alert">{error}</p>}
    </Popup>
  )
}
