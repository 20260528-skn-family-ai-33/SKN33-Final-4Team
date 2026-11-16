// 가운데 창: 머니쿠션 목표 바꾸기 — 쿠션 화면 [목표 바꾸기] (11/13)
// 목표 금액을 직접 입력한다. 기본은 월급의 3배(월급이 바뀌면 같이 바뀜), 직접 정하면 월급이 바뀌어도 그대로
// 쿠션 필수 판단 · 지킬 돈의 '목표까지 N개월'도 같은 목표를 쓴다
// PATCH /api/budgets/cushion/ { goalAmount } (null = 기본으로 되돌리기, docs/api/cushion.md). 월 쿠션 적립금은 지킬 돈 설정에서 바꾼다
// props: cushion = 쿠션 화면이 받은 값 (goal · goalCustom · defaultGoal), onSaved = 바꾼 뒤 쿠션 화면 다시 불러오기
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import MoneyInput from '../components/MoneyInput.jsx'
import { apiSend } from '../utils/api.js'
import { won } from '../utils/money.js'
import { toast } from '../utils/toast.js'

const MIN = 10_000
const MAX = 1_000_000_000

export default function CushionPopup({ cushion, onSaved }) {
  const { closePopup } = usePopup()
  const [digits, setDigits] = useState(cushion.goal == null ? '' : String(cushion.goal))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const send = async (goalAmount, message) => {
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/budgets/cushion/', 'PATCH', { goalAmount })
      onSaved?.()
      closePopup()
      toast(message)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  const save = (e) => {
    e.preventDefault()
    if (busy) return
    const amount = Number(digits)
    if (digits === '') { setError('목표 금액을 입력해 주세요.'); return }
    if (amount < MIN || amount > MAX) { setError(`${won(MIN)}부터 ${won(MAX)}까지 정할 수 있어요.`); return }
    if (cushion.goalCustom && amount === cushion.goal) { closePopup(); return }
    send(amount, `목표를 ${won(amount)}으로 바꿨어요`)
  }

  return (
    <Popup label="쿠션 목표 바꾸기" dialog>
      <h2>목표를 얼마로 할까요?</h2>
      <p className="mc-muted" style={{ margin: '6px 0 0' }}>갑자기 큰돈이 필요할 때를 대비해 모아 둘 금액이에요.</p>
      <form onSubmit={save}>
        <label className="mc-field mc-cushion-field">목표 금액
          <MoneyInput value={digits} onChange={(d) => { setDigits(d); setError('') }} placeholder="예: 5,000,000" aria-label="목표 금액 (원)" />
        </label>
        {cushion.defaultGoal != null && <p className="mc-muted mc-cushion-hint">추천 {won(cushion.defaultGoal)} · 월급의 3배</p>}
        {error && <p className="mc-error" role="alert">{error}</p>}
        <button type="submit" className="mc-primary" disabled={busy}>{busy ? '저장 중…' : '저장'}</button>
      </form>
      {cushion.goalCustom && (
        <button type="button" className="mc-link mc-cushion-reset" disabled={busy} onClick={() => send(null, '목표를 추천 금액으로 되돌렸어요')}>
          추천 금액으로 되돌리기
        </button>
      )}
    </Popup>
  )
}
