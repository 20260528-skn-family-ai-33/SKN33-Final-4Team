// 카드번호 표시 · 입력 도우미 (components/CardNoInput.jsx)

export const MASK = '•'  // 비밀번호 칸(type=password)과 같은 동그라미 (11/9)
export const MAX = 19  // 카드번호 최대 자릿수 (CODEF cardNo)

export function cardNoText(digits, show) {
  const chars = show ? digits : digits.slice(0, 4) + MASK.repeat(Math.max(digits.length - 4, 0))
  return chars.match(/.{1,4}/g)?.join(' - ') ?? ''  // 화면만 ' - ', 보내는 값은 숫자만
}

export function nextDigits(digits, typed, show) {
  const prev = cardNoText(digits, show).replace(/[\s-]/g, '')
  const cur = typed.replace(/[\s-]/g, '')
  let p = 0
  while (p < prev.length && p < cur.length && prev[p] === cur[p]) p++
  let s = 0
  while (s < prev.length - p && s < cur.length - p && prev[prev.length - 1 - s] === cur[cur.length - 1 - s]) s++
  const inserted = cur.slice(p, cur.length - s).replace(/\D/g, '')
  return (digits.slice(0, p) + inserted + digits.slice(digits.length - s)).slice(0, MAX)
}
