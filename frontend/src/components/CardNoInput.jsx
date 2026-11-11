// 카드번호 입력 칸 (11/9): 4자리마다 ' - ', 기본은 앞 4자리만 보이고 나머지는 '•'. [보기]를 눌렀을 때만 전체
// props: value = 숫자만 (부모 상태), onChange(숫자만), 나머지(name · disabled · aria-invalid 등)는 <input>에 그대로
// 가린 칸에서 고쳐도 숫자가 맞게: 입력 직후 커서 위치 · 입력 종류로 바뀐 자리를 찾는다 (utils/cardNo.js editDigits, PR #101 리뷰)
// 화면 글자가 다시 만들어지면 커서가 칸 끝으로 가서 → 고친 자리 바로 뒤로 커서를 되돌린다 (가운데를 연달아 지워도 같은 자리)
import { useLayoutEffect, useRef, useState } from 'react'
import { MAX, caretIndex, cardNoText, editDigits } from '../utils/cardNo.js'

export default function CardNoInput({ value, onChange, ...rest }) {
  const [show, setShow] = useState(false)
  const input = useRef(null)
  const caret = useRef(null)  // { digits, at } — 이 값으로 다시 그려졌을 때만 커서를 옮긴다
  useLayoutEffect(() => {
    const want = caret.current
    if (!want || want.digits !== value) return
    caret.current = null
    const el = input.current
    if (el && document.activeElement === el) {
      const pos = Math.min(caretIndex(want.at), el.value.length)
      el.setSelectionRange(pos, pos)
    }
  })
  const change = (e) => {
    const next = editDigits(value, e.target.value, show, e.target.selectionStart, e.nativeEvent?.inputType)
    caret.current = next
    onChange(next.digits)
  }
  return (
    <span className="mc-cardno">
      <input ref={input} autoComplete="off" inputMode="numeric" spellCheck={false} maxLength={MAX + 12} {...rest}
        value={cardNoText(value, show)} onChange={change} />
      <button type="button" className="mc-link" aria-pressed={show} onClick={() => setShow((v) => !v)}>{show ? '숨기기' : '보기'}</button>
    </span>
  )
}
