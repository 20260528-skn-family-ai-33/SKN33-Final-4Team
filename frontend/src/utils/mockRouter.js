// 임시 라우터 — 백엔드 LLM 라우터(/api/chat/)가 생기기 전까지 화면 흐름만 보여 주는 용도
// 문장 속 단어로 대충 나눈다. 진짜 라우터가 붙으면 이 파일 대신 API 응답(intent·slots)을 쓴다
//   반환: { intent: 'spending' | 'budget' | 'big' | 'out' | 'unknown' }

const RULES = [
  ['out', /투자|주식|코인|펀드|etf/i],               // 서비스 범위 밖
  ['big', /큰 지출|목돈|보증금|이사|대출|복지/],       // 큰 지출 상담 (3차, 준비 중)
  ['budget', /예산|조정|늘리|줄이|바꾸고/],          // 예산 조정
  ['spending', /소비|지출|썼|쓴|분석|내역|얼마나/],   // 소비 분석
]

export function mockRoute(text) {
  const hit = RULES.find(([, re]) => re.test(text))
  return { intent: hit ? hit[0] : 'unknown' }
}

// 문장 속 재료(슬롯) 뽑기 — 예산 조정용. 진짜 라우터가 붙으면 API 의 slots 를 쓴다
//   "식비 5만원 늘리고 싶어" → { cat: '식비', amount: 50000, dir: 'up' }  (amount 단위: 원 — API 규칙과 같게. 없으면 그 칸은 빠짐)
const CAT_WORDS = [   // 위에서부터 먼저 맞는 것 (10/20 분류 기준: 헬스·운동 = 문화·여가, 병의원 = 의료·건강)
  ['의료·건강', /병원|의원|약국|치과|한의원|안경|영양제|건강/],
  ['뷰티·미용', /미용|화장품|네일|피부관리|헤어/],
  ['교육', /교육|학원|강의|책|서점|문구|스터디|자격증/],
  ['여행·숙박', /여행|숙박|호텔|펜션|모텔|캠핑/],
  ['패션·쇼핑', /패션|쇼핑|옷|의류|신발|가방|액세서리/],
  ['문화·여가', /여가|문화|취미|영화|공연|게임|헬스|운동|노래방|구독/],
  ['식비', /식비|밥|식사|배달|외식|카페|커피|술/],
  ['주거·통신', /주거|월세|관리비|통신|공과금/],
  ['교통·차량', /교통|차량|주유|기름|택시|버스|지하철|주차/],
  ['생활용품', /생활용품|생필품|가전|가구|휴대폰|핸드폰/],
  ['기타', /기타|경조사|축의금|부조/],
]

export function mockSlots(text) {
  const slots = {}
  const cat = CAT_WORDS.find(([, re]) => re.test(text))
  if (cat) slots.cat = cat[0]
  const man = text.match(/(\d+)\s*만/)            // 5만, 5만원
  const won = text.match(/(\d{4,})\s*원/)          // 50000원
  if (man) slots.amount = Number(man[1]) * 10_000
  else if (won) slots.amount = Number(won[1])
  if (/늘리|늘려|올리|올려|더 쓰/.test(text)) slots.dir = 'up'
  else if (/줄이|줄여|내리|내려|아끼/.test(text)) slots.dir = 'down'
  return slots
}
