// 카드번호 표시 · 입력 도우미 (components/CardNoInput.jsx)

export const MASK = '•'  // 비밀번호 칸(type=password)과 같은 동그라미 (11/9)
export const MAX = 19  // 카드번호 최대 자릿수 (CODEF cardNo)

const strip = (text) => text.replace(/[\s-]/g, '')

export function cardNoText(digits, show) {
  const chars = show ? digits : digits.slice(0, 4) + MASK.repeat(Math.max(digits.length - 4, 0))
  return chars.match(/.{1,4}/g)?.join(' - ') ?? ''  // 화면만 ' - ', 보내는 값은 숫자만
}

// 숫자 n개 뒤 커서가 화면 칸의 몇 번째 글자 뒤인지 (' - ' 앞에 둔다)
export function caretIndex(n) {
  return n + 3 * Math.floor(Math.max(n - 1, 0) / 4)
}

// 입력 한 번 → { digits: 새 숫자, at: 바뀐 곳 끝(숫자 기준 커서) }
// caret = 입력 직후 커서 위치(input.selectionStart). 커서 뒤 글자는 이번 입력으로 안 바뀌므로 그만큼을 뒤에서 그대로 두고,
//   앞에서 같은 글자는 커서까지만 같다고 본다 — 가린 칸(•)처럼 같은 글자가 이어져도 지운 자리를 정확히 안다 (PR #101 리뷰)
// caret 이 없으면(커서를 못 읽는 경우) 예전처럼 같은 앞 · 뒤를 빼고 가운데만 바뀐 것으로 본다
// kind = 입력 종류(InputEvent.inputType). ' - ' 의 띄어쓰기 · '-' 만 지워졌으면 지운 방향의 옆 숫자를 지운다
//   (Backspace = 앞 숫자, Delete = 뒤 숫자 — 안 그러면 다시 그려지며 아무 일도 안 일어난다)
export function editDigits(digits, typed, show, caret, kind) {
  const prev = strip(cardNoText(digits, show))
  const cur = strip(typed)
  if (caret != null && cur === prev && typed.length < cardNoText(digits, show).length) {
    const end = strip(typed.slice(0, caret)).length
    if (kind === 'deleteContentBackward' && end > 0) return { digits: digits.slice(0, end - 1) + digits.slice(end), at: end - 1 }
    if (kind === 'deleteContentForward' && end < digits.length) return { digits: digits.slice(0, end) + digits.slice(end + 1), at: end }
  }
  let p = 0
  while (p < prev.length && p < cur.length && prev[p] === cur[p]) p++
  let s = 0
  if (caret == null) {
    while (s < prev.length - p && s < cur.length - p && prev[prev.length - 1 - s] === cur[cur.length - 1 - s]) s++
  } else {
    const end = Math.min(strip(typed.slice(0, caret)).length, cur.length)
    s = Math.min(cur.length - end, prev.length)
    p = Math.min(p, end, prev.length - s)
  }
  const inserted = cur.slice(p, cur.length - s).replace(/\D/g, '')
  const next = (digits.slice(0, p) + inserted + digits.slice(digits.length - s)).slice(0, MAX)
  return { digits: next, at: Math.min(p + inserted.length, next.length) }
}

export function nextDigits(digits, typed, show, caret, kind) {
  return editDigits(digits, typed, show, caret, kind).digits
}
