// 팝업: 카테고리 최소 금액 — 내 정보 입력의 "카테고리 최소 금액 설정" (2026-10-21)
// 원하는 카테고리만 금액을 넣는다. 비워 두면 설정 안 함 (우선순위와 상관없이 어느 카테고리든 가능)
// 자물쇠와 다름: 자물쇠 = 금액 그대로 고정 / 최소 금액 = 이 금액 밑으로만 안 내려감(올리는 건 가능)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import Icon from '../components/Icon.jsx'
import { BUDGET_CATS, catIcon } from '../utils/categories.js'
import { comma, won } from '../utils/money.js'

// props: value = { 식비: 300000, ... } 지금 설정값 / onSave(next) = 저장 누르면 새 값을 돌려준다
export default function CatMinPopup({ value = {}, onSave }) {
  const { closePopup } = usePopup()
  // 입력칸 글자 그대로 들고 있다가 저장할 때 숫자로 바꾼다 (쉼표 표시용)
  const [draft, setDraft] = useState(() => Object.fromEntries(BUDGET_CATS.map((c) => [c, value[c] ? comma(value[c]) : ''])))
  const onType = (cat, raw) => {
    const digits = raw.replace(/\D/g, '').replace(/^0+/, '').slice(0, 10)
    setDraft((now) => ({ ...now, [cat]: digits ? comma(digits) : '' }))
  }
  const next = Object.fromEntries(
    Object.entries(draft).map(([c, v]) => [c, Number(v.replace(/\D/g, ''))]).filter(([, n]) => n > 0)
  )
  const total = Object.values(next).reduce((a, b) => a + b, 0)

  const onSubmit = (e) => {
    e.preventDefault()
    onSave?.(next)  // 저장은 API 연결 후 (category_minimum)
    closePopup()
  }

  return (
    <Popup label="카테고리 최소 금액" sub>
      <div className="mc-eyebrow">카테고리 최소 금액 · 선택</div>
      <h1>이만큼은 꼭 남겨 둘<br />카테고리가 있나요?</h1>
      <p className="mc-muted">넣은 금액 밑으로는 예산을 줄이지 않아요. 필요 없는 카테고리는 비워 두세요.</p>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        <div className="mc-min-list">
          {BUDGET_CATS.map((c) => (
            <label className={draft[c] ? 'mc-min-row on' : 'mc-min-row'} key={c}>
              <span className="mc-catname"><span className="mc-cat-ic"><Icon name={catIcon(c)} /></span>{c}</span>
              <span className="mc-min-input">
                <input aria-label={`${c} 최소 금액`} inputMode="numeric" placeholder="설정 안 함" type="text"
                  value={draft[c]} onChange={(e) => onType(c, e.target.value)} />
                <i>원</i>
              </span>
            </label>
          ))}
        </div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>
          금액을 그대로 고정하고 싶다면 추천 예산에서 <b>자물쇠</b>를 걸어 주세요. 최소 금액은 더 늘어날 수는 있어요.
        </p>
        <div className="mc-foot">
          <button className="mc-primary" form="mc-pop-form" type="submit">
            {total > 0 ? `${Object.keys(next).length}개 · 합계 ${won(total)} 저장` : '설정 안 하고 닫기'}
          </button>
        </div>
      </form>
    </Popup>
  )
}
