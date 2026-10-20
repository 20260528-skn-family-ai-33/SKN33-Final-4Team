// 예산 조정 고르기 말풍선: "(카테고리)를 (금액) 만큼 (늘리고/줄이고) 싶어"
// 통합 챗봇과 추천 예산 "챗봇과 조정하기" 팝업이 같이 쓴다
// props: initial = { cat, amount(만원), dir: 'up' | 'down' } 미리 채울 값 (문장에서 뽑은 재료)
//        onSubmit({ cat, amount, dir }) = "조정안 받기"를 눌렀을 때 / done = 이미 보낸 말풍선이면 true (잠금)
import { useState } from 'react'
import { CATS, josa } from '../utils/adjust.js'

const AMOUNTS = [1, 3, 5, 10]  // 만원
const DIRS = [['up', '늘리고 싶어'], ['down', '줄이고 싶어']]

export default function AdjustForm({ initial = {}, onSubmit, done = false }) {
  const [cat, setCat] = useState(initial.cat ?? '')
  const preset = AMOUNTS.includes(initial.amount) ? initial.amount : null
  const [amount, setAmount] = useState(preset)                                       // 버튼으로 고른 금액
  const [custom, setCustom] = useState(preset == null && initial.amount ? String(initial.amount) : '')  // 직접 입력
  const [dir, setDir] = useState(initial.dir ?? null)
  const [error, setError] = useState('')

  const picked = amount ?? (Number(custom) > 0 ? Number(custom) : null)

  const submit = (e) => {
    e.preventDefault()
    if (!cat) return setError('카테고리를 골라 주세요.')
    if (!picked) return setError('금액을 골라 주세요.')
    if (!dir) return setError('늘릴지 줄일지 골라 주세요.')
    setError('')
    onSubmit?.({ cat, amount: picked, dir })
  }

  return (
    <form className="mc-cf" onSubmit={submit}>
      <fieldset disabled={done} className="mc-cf-set">
        <div className="mc-cf-line">
          <select aria-label="카테고리" value={cat} onChange={(e) => { setCat(e.target.value); setError('') }}>
            <option value="" disabled>카테고리</option>
            {CATS.map((c) => <option key={c}>{c}</option>)}
          </select>
          <span>{cat ? josa(cat) : '을/를'}</span>
        </div>
        <div className="mc-cf-amts">
          {AMOUNTS.map((a) => (
            <button key={a} type="button" aria-pressed={amount === a}
              onClick={() => { setAmount(a); setCustom(''); setError('') }}>{a}만원</button>
          ))}
          <label className="mc-cf-custom">
            <input aria-label="직접 입력 (만원)" inputMode="numeric" placeholder="직접" type="text" value={custom}
              onChange={(e) => { setCustom(e.target.value.replace(/\D/g, '')); setAmount(null); setError('') }} />
            <span>만원</span>
          </label>
        </div>
        <div className="mc-cf-line">
          <span>만큼</span>
          <div className="mc-cf-dir">
            {DIRS.map(([key, label]) => (
              <button key={key} type="button" aria-pressed={dir === key} onClick={() => { setDir(key); setError('') }}>{label}</button>
            ))}
          </div>
        </div>
        {error && <p className="mc-error mc-cf-err" role="alert">{error}</p>}
        <button type="submit" className="mc-primary mc-cf-go">{done ? '보냈어요' : '조정안 받기'}</button>
      </fieldset>
    </form>
  )
}
