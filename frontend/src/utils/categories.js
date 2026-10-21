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

// 항목 이름으로 카테고리 미리 고르기 (고정지출 추가 등) — 못 찾으면 null, 사용자가 직접 고른다
// 위에서부터 먼저 맞는 것 (10/20 분류 기준: 헬스·운동 = 문화·여가). 자동차보험이 일반 보험보다 먼저
const GUESS = [
  ['주거·통신', /월세|관리비|전세|통신|휴대폰|핸드폰|인터넷|와이파이|전기|가스|수도|공과금/],
  ['교통·차량', /교통|버스|지하철|주차|주유|자동차보험|차량/],
  ['문화·여가', /넷플릭스|유튜브|멜론|스포티파이|티빙|웨이브|왓챠|디즈니|쿠팡플레이|구독|ott|헬스|필라테스|요가|운동/i],
  ['교육', /학원|강의|인강|교육|스터디|자격증|도서/],
  ['의료·건강', /보험|병원|약국|영양제/],
  ['뷰티·미용', /미용|헤어|네일|피부/],
  ['생활용품', /정기배송|생수|렌탈|정수기/],
]
export function guessCat(name) {
  const hit = GUESS.find(([, re]) => re.test(name ?? ''))
  return hit ? hit[0] : null
}

const ICONS = Object.fromEntries([...SPEND_CATS, ETC, PENDING].map((c) => [c.name, c.icon]))
export const catIcon = (name) => ICONS[name] ?? 'Circle'
