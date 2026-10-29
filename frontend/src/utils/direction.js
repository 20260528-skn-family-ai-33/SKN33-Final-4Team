// 자취 여부 이름 · 비율 (내 정보 화면). 지킬 돈 · 추천 예산 화면은 서버 계산(GET /api/budgets/protect/)을 쓴다 (10/28)
// 아래 recommendFor · applySavingsMin 은 서버 계산(budgets/protect.py)과 같은 규칙의 화면용 사본 — 화면에서는 더 이상 안 쓰고 테스트로만 남아 있다
// 월급·고정지출은 시안 예시값
//
// 배분 기준 금액 = 월 실수령액 - 확정 고정지출
// 내 정보 입력에서 고른 자취 여부로 비율을 정한다 (2026-10-27, 생활 안정형/저축 집중형 선택을 대체)
//              생활비   저축   여윳돈   쿠션 적립
//   자취         55%    30%     5%      10%
//   본가 거주    45%    40%     5%      10%
// 생활비·저축은 1만원 단위 반올림, 쿠션은 1만원 단위 내림, 나머지(반올림 차이 포함)는 여윳돈
// 생활비는 다음 화면(추천 예산 확인)에서 카테고리별로 나눈다

export const MONTHLY = 2_800_000   // 월급
export const FIXED = 770_000       // 고정지출 합계
export const CUSHION_GOAL = MONTHLY * 3   // 머니 쿠션 목표 = 월급×3

// 거주 형태별 이름 · 버튼 글자 · 비율
export const HOUSING = {
  alone: { name: '자취', label: '혼자 살아요', sub: '자취 · 독립', rates: { living: 0.55, saving: 0.30, spare: 0.05, cushion: 0.10 } },
  family: { name: '본가 거주', label: '가족과 살아요', sub: '본가 거주', rates: { living: 0.45, saving: 0.40, spare: 0.05, cushion: 0.10 } },
}
export const HOUSING_KEYS = Object.keys(HOUSING)
const DEFAULT_HOUSING = 'alone'

// 화면에 보여 줄 비율 한 줄: "생활비 55% · 저축 30% · 여윳돈 5% · 쿠션 10%"
const RATE_LABELS = [['living', '생활비'], ['saving', '저축'], ['spare', '여윳돈'], ['cushion', '쿠션']]
export const rateLine = (housing) => {
  const r = (HOUSING[housing] ?? HOUSING[DEFAULT_HOUSING]).rates
  return RATE_LABELS.map(([k, n]) => `${n} ${Math.round(r[k] * 100)}%`).join(' · ')
}

// 내 정보 입력에서 고른 자취 여부를 첫 설정 흐름 동안만 기억한다 (저장은 API 연결 후 financial_profile 로)
const KEY = 'mc-housing'
export function loadHousing() {
  try { const v = window.sessionStorage.getItem(KEY); return v && v in HOUSING ? v : null } catch { return null }
}
export function saveHousing(v) {
  try { window.sessionStorage.setItem(KEY, v) } catch { /* 저장 못 해도 화면은 기본값(자취)으로 동작 */ }
}

const round10k = (n) => Math.round(n / 10_000) * 10_000
const floor10k = (n) => Math.floor(n / 10_000) * 10_000

// housing: 'alone' | 'family'. 모르면 자취 기준
export function recommendFor(housing) {
  const key = housing in HOUSING ? housing : DEFAULT_HOUSING
  const { rates } = HOUSING[key]
  const available = Math.max(0, MONTHLY - FIXED)
  const livingBudget = round10k(available * rates.living)
  const savingFloor = round10k(available * rates.saving)
  const cushionMonthly = floor10k(available * rates.cushion)
  const spare = Math.max(0, available - livingBudget - savingFloor - cushionMonthly)
  return { housing: key, rates, available, livingBudget, savingFloor, cushionMonthly, spare }
}

// 저축 최소 금액 적용 (저축 확인에서 '저축이에요'로 고른 이체의 월 합계) — 2026-10-27 결정
//   최소 금액 ≤ 추천 저축 → 추천 그대로
//   최소 금액 > 추천 저축 → 저축 = 최소 금액. 남은 금액(배분 기준 - 저축)을 생활비 : 여윳돈 : 쿠션 원래 비율로 다시 나눈다
//                           (본가 45 : 5 : 10, 자취 55 : 5 : 10). 생활비 · 쿠션은 1만원 단위 내림, 자투리는 여윳돈
//   남은 금액 ≤ 0          → 나눌 수 없음(blocked). 부족액(deficit)을 보여 주고 저축 · 고정지출을 고치게 한다
export function applySavingsMin(rec, savingsMin = 0) {
  if (!(savingsMin > rec.savingFloor)) return { ...rec, savingsMin, rebalanced: false, blocked: false, deficit: 0 }
  const rest = rec.available - savingsMin
  if (rest <= 0) {
    return { ...rec, savingsMin, savingFloor: savingsMin, livingBudget: 0, cushionMonthly: 0, spare: 0, rebalanced: true, blocked: true, deficit: -rest }
  }
  // 소수 비율의 합(예: 0.55 + 0.05 + 0.10)의 오차로 1만원이 덜 배분되지 않도록 정수로 계산
  const living = Math.round(rec.rates.living * 100)
  const spare = Math.round(rec.rates.spare * 100)
  const cushion = Math.round(rec.rates.cushion * 100)
  const sum = living + spare + cushion
  const livingBudget = floor10k((rest * living) / sum)
  const cushionMonthly = floor10k((rest * cushion) / sum)
  return {
    ...rec, savingsMin, savingFloor: savingsMin, livingBudget, cushionMonthly,
    spare: rest - livingBudget - cushionMonthly, rest, rebalanced: true, blocked: false, deficit: 0,
  }
}

// 다시 나눌 때 쓰는 비율 한 줄: "생활비 : 여윳돈 : 쿠션 = 45 : 5 : 10"
export const restRatioLine = (housing) => {
  const r = (HOUSING[housing] ?? HOUSING[DEFAULT_HOUSING]).rates
  const n = (v) => Math.round(v * 100)
  return `생활비 : 여윳돈 : 쿠션 = ${n(r.living)} : ${n(r.spare)} : ${n(r.cushion)}`
}

// 매달 perMonth 씩 모으면 쿠션 목표까지 몇 달 (0 이하면 null)
export const monthsToGoal = (perMonth) => (perMonth > 0 ? Math.ceil(CUSHION_GOAL / perMonth) : null)
