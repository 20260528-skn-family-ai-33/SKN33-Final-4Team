// 예산 산정 방향 · 지킬 돈 추천 계산
// 예산 산정 방향(미리보기)과 지킬 돈 설정 화면이 같은 계산을 쓴다
// 월급·고정지출·비율은 시안 예시값 — 추천 API 가 생기면 바꾼다
//
// 배분 가능한 금액 = 월급 - 고정지출
//   생활비 = 배분 가능 × 생활비 비율   (한 달 생활비 전체. 다음 화면에서 카테고리별로 나눈다)
//   저축   = 배분 가능 × 저축 비율
//   남는 돈 = 배분 가능 - 생활비 - 저축 - 비정기지출 준비금
//   월 쿠션 적립금 = 남는 돈의 절반 (1만원 단위 내림) → 나머지는 여윳돈

export const MONTHLY = 2_800_000   // 월급
export const FIXED = 770_000       // 고정지출 합계
export const IRREGULAR = 78_000    // 비정기지출 준비금
export const CUSHION_GOAL = MONTHLY * 3   // 머니 쿠션 목표 = 월급×3

// [이름, 설명, 생활비 비율, 저축 비율]
export const DIRS = [
  ['생활 안정형', '생활은 안정되게, 저축은 최소한으로만', 0.55, 0.20],
  ['저축 집중형', '생활을 빠듯하게, 저축에 집중', 0.40, 0.45],
]

const round10k = (n) => Math.round(n / 10_000) * 10_000
const floor10k = (n) => Math.floor(n / 10_000) * 10_000

// dir: DIRS 의 순번(0·1). 고르지 않았으면 생활 안정형 기준
export function recommendFor(dir) {
  const [, , livingRate, savingRate] = DIRS[dir] ?? DIRS[0]
  const available = Math.max(0, MONTHLY - FIXED)
  const livingBudget = round10k(available * livingRate)
  const savingFloor = round10k(available * savingRate)
  const rest = available - livingBudget - savingFloor - IRREGULAR
  const cushionMonthly = Math.max(0, floor10k(rest * 0.5))
  return { available, livingRate, savingRate, livingBudget, savingFloor, cushionMonthly }
}

// 저축 최소 금액(저축 확인에서 '저축이에요'로 고른 이체의 월 합계) 적용 — 요구사항 BUD-003
// 추천 저축이 최소 금액보다 작으면 저축을 최소 금액까지 올리고 그만큼 생활비를 줄인다 (생활비는 0원 아래로 안 내림)
export function applySavingsMin(rec, savingsMin = 0) {
  if (!(savingsMin > rec.savingFloor)) return rec
  const raise = savingsMin - rec.savingFloor
  return { ...rec, savingFloor: savingsMin, livingBudget: Math.max(0, rec.livingBudget - raise) }
}

// 매달 perMonth 씩 모으면 쿠션 목표까지 몇 달 (0 이하면 null)
export const monthsToGoal = (perMonth) => (perMonth > 0 ? Math.ceil(CUSHION_GOAL / perMonth) : null)
