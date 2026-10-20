// 소비 카테고리 — 2026-10-20 팀 분류 기준 (가게 업종 기준 · 10개 + 상태값 2개)
// 화면에서 카테고리 목록이 필요하면 전부 여기서 가져다 쓴다. 기준이 바뀌면 이 파일만 고치면 된다
// 백엔드 category 초기 데이터(DB)와 이름·순서를 맞춘다

// 10개 카테고리 (ML 정답 · 우선순위 · 예산 대상)
export const SPEND_CATS = [
  { name: '식비', icon: 'Utensils' },
  { name: '주거·통신', icon: 'House' },
  { name: '교통·차량', icon: 'Bus' },
  { name: '생활용품', icon: 'ShoppingBasket' },
  { name: '패션·쇼핑', icon: 'Shirt' },
  { name: '의료·건강', icon: 'Stethoscope' },
  { name: '뷰티·미용', icon: 'Scissors' },
  { name: '교육', icon: 'GraduationCap' },
  { name: '문화·여가', icon: 'Ticket' },
  { name: '여행·숙박', icon: 'Plane' },
]

// 상태값 (10개 밖. ML 정답이 아님)
// 기타: 10개 범위 밖이라고 판단된 지출 (송금·경조사·담배·세탁 등)
//   budget: 예산 칸을 줄지. 빼기로 하면 false 로만 바꾸면 된다 (10/20 결정: 준다)
export const ETC = { name: '기타', icon: 'Ellipsis', budget: true }
// 미분류: 아직 판단 못 한 지출 (편의점·마트·종합몰). 화면에는 '확인 필요'로 보여 준다 (10/20 결정)
export const PENDING = { name: '확인 필요', icon: 'CircleQuestionMark' }

export const CAT_NAMES = SPEND_CATS.map((c) => c.name)                       // 우선순위 고르기
export const BUDGET_CATS = [...CAT_NAMES, ...(ETC.budget ? [ETC.name] : [])]  // 예산 칸이 있는 카테고리
export const PICK_CATS = [...CAT_NAMES, ETC.name]                           // 사용자가 거래 카테고리를 고를 때

const ICONS = Object.fromEntries([...SPEND_CATS, ETC, PENDING].map((c) => [c.name, c.icon]))
export const catIcon = (name) => ICONS[name] ?? 'Circle'
