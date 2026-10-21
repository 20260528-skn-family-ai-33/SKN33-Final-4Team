// 금액 입력칸: 화면에는 쉼표를 넣어 보여 주고(33000 → 33,000), 바깥으로는 숫자만 넘긴다
// 쓰는 법 두 가지
//   1) 값을 직접 관리: <MoneyInput value={digits} onChange={(digits) => ...} />   (digits = '33000' 같은 숫자 문자열, 빈 칸은 '')
//   2) 폼에 맡김:      <MoneyInput name="salary" defaultValue={2800000} />          (같은 name 의 숨은 칸에 숫자만 담겨 폼으로 전송)
// 그 밖의 props(className, id, placeholder, aria-label 등)는 그대로 <input> 에 붙는다
import { useLayoutEffect, useRef, useState } from 'react'

const MAX_DIGITS = 13  // 9,999,999,999,999원까지
export const onlyDigits = (s) => String(s ?? '').replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, MAX_DIGITS)
const withComma = (d) => (d === '' ? '' : Number(d).toLocaleString('ko-KR'))

export default function MoneyInput({ value, defaultValue, onChange, name, ...rest }) {
  const controlled = value !== undefined
  const [inner, setInner] = useState(() => onlyDigits(defaultValue))
  const digits = controlled ? onlyDigits(value) : inner
  const ref = useRef(null)
  const caret = useRef(null)  // 커서 앞에 있던 숫자 개수

  // 쉼표가 들어가거나 빠져도 커서가 같은 숫자 뒤에 남도록 다시 맞춘다
  useLayoutEffect(() => {
    const el = ref.current
    if (caret.current == null || !el || document.activeElement !== el) return
    let left = caret.current
    let pos = 0
    while (pos < el.value.length && left > 0) {
      if (/\d/.test(el.value[pos])) left--
      pos++
    }
    el.setSelectionRange(pos, pos)
    caret.current = null
  })

  const handleChange = (e) => {
    const el = e.target
    caret.current = el.value.slice(0, el.selectionStart ?? el.value.length).replace(/\D/g, '').length
    const next = onlyDigits(el.value)
    if (!controlled) setInner(next)
    onChange?.(next, e)
  }

  return (
    <>
      <input ref={ref} type="text" inputMode="numeric" autoComplete="off" value={withComma(digits)} onChange={handleChange} {...rest} />
      {name && <input type="hidden" name={name} value={digits} />}
    </>
  )
}
