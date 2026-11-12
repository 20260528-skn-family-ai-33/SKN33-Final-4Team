// 비밀번호 입력 칸 (11/12 피드백): 기본은 가리고, [보기]를 누르면 입력한 글자를 그대로 보여 준다
// 모양 · 동작은 카드번호 칸(CardNoInput)의 [보기]와 같다 — 입력란 안 오른쪽 끝, 다시 누르면 [숨기기]
// props: <input>에 그대로 넘긴다 (name · value · onChange · autoComplete · disabled · aria-* 등). type 은 이 칸이 정한다
import { useState } from 'react'

export default function PasswordInput({ disabled, ...rest }) {
  const [show, setShow] = useState(false)
  return (
    <span className="mc-pwinput">
      <input spellCheck={false} {...rest} disabled={disabled} type={show ? 'text' : 'password'} />
      <button type="button" className="mc-link" aria-pressed={show} disabled={disabled} onClick={() => setShow((v) => !v)}>{show ? '숨기기' : '보기'}</button>
    </span>
  )
}
