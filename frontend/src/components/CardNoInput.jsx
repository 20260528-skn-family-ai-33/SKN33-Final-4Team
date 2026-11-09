// 카드번호 입력 칸 (11/9): 4자리마다 ' - ', 기본은 앞 4자리만 보이고 나머지는 '•'. [보기]를 눌렀을 때만 전체
// props: value = 숫자만 (부모 상태), onChange(숫자만), 나머지(name · disabled · aria-invalid 등)는 <input>에 그대로
// 가린 칸에서 고쳐도 숫자가 맞게: 이전 표시와 새 표시의 같은 앞 · 뒤를 빼고, 가운데 새로 친 숫자만 끼운다
// shortcut: '-'가 새로 붙을 때 커서가 칸 끝으로 갈 수 있음 (가운데 고칠 때만 불편), 불편하다는 말이 나오면 selectionStart 보정 추가
import { useState } from 'react'
import { MAX, cardNoText, nextDigits } from '../utils/cardNo.js'

export default function CardNoInput({ value, onChange, ...rest }) {
  const [show, setShow] = useState(false)
  return (
    <span className="mc-cardno">
      <input autoComplete="off" inputMode="numeric" spellCheck={false} maxLength={MAX + 12} {...rest}
        value={cardNoText(value, show)} onChange={(e) => onChange(nextDigits(value, e.target.value, show))} />
      <button type="button" className="mc-link" aria-pressed={show} onClick={() => setShow((v) => !v)}>{show ? '숨기기' : '보기'}</button>
    </span>
  )
}
