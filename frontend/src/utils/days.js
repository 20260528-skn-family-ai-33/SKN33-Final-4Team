// 매월 1일 ~ 31일 (출금일·납부일 고르기)
export const MONTH_DAYS = Array.from({ length: 31 }, (_, i) => i + 1)

// 그 달의 'N일'. 그 달에 그날이 없으면(예: 2월 31일) 그 달 말일로 (2/28, 윤년 2/29)
// 10-21 확정 규칙 (요구사항 BUD-004 · docs/API_RULES.md '매월 N일') — 급여일 · 고정지출 출금일 · 대출 납부일 공통
// month 는 0부터 (1월 = 0), 범위를 넘어도 Date 가 알아서 연도를 넘긴다
export function dayIn(year, month, day) {
  const last = new Date(year, month + 1, 0).getDate()
  return new Date(year, month, Math.min(day, last))
}
const paydayIn = dayIn

// 오늘이 속한 급여 주기 { start, end, days }
// start = 가장 최근 급여일, end = 다음 급여일 (end 는 다음 주기 첫날이라 포함하지 않음), days = 실제 일수
// 예: payCycle(25) → 9.25 ~ 10.24, 30일
export function payCycle(payday, today = new Date()) {
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  let start = paydayIn(t.getFullYear(), t.getMonth(), payday)
  if (start > t) start = paydayIn(t.getFullYear(), t.getMonth() - 1, payday)
  const end = paydayIn(start.getFullYear(), start.getMonth() + 1, payday)
  return { start, end, days: Math.round((end - start) / 86_400_000) }
}

// 10.25 처럼 짧게
export const shortDate = (d) => `${d.getMonth() + 1}.${String(d.getDate()).padStart(2, '0')}`
