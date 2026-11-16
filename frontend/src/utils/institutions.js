// 기관별로 로그인 때 더 받아야 하는 값 (CODEF 개발가이드 기준, 2026-11-13 확인)
// 진짜 연동 때도 그대로 쓰는 규칙이라 mock 이 아니다. 기관이 늘면 여기에만 추가한다
//   fields: 아이디·비밀번호 말고 더 받을 칸 / note: 로그인 전에 보여 줄 안내 / warn: 주의 문구

export const FIELD = {
  cardNo: { label: '카드번호', placeholder: '숫자만 16자리', inputMode: 'numeric', maxLength: 16 },
  cardPw4: { label: '카드 비밀번호 4자리', type: 'password', inputMode: 'numeric', maxLength: 4 },
  cardPw2: { label: '카드 비밀번호 앞 2자리', type: 'password', inputMode: 'numeric', maxLength: 2 },
  cardPw: { label: '카드 비밀번호 (2자리 또는 4자리)', type: 'password', inputMode: 'numeric', maxLength: 4 },
}

export const EXTRA = {
  현대카드: { fields: ['cardNo', 'cardPw4'], warn: '카드 비밀번호를 3번 틀리면 카드사 계정이 잠겨요.' },
  하나카드: { fields: ['cardNo', 'cardPw2'] },
  국민카드: { note: '카드사에서 카드 소지 확인을 요청하면 카드번호를 한 번 더 물어볼 수 있어요.' },
  농협은행: { note: 'e농협 홈페이지에서 "조회계좌"로 등록한 계좌만 불러올 수 있어요.' },
  씨티은행: { note: '씨티은행은 로그인할 때 휴대폰 문자 인증을 요청할 수 있어요.' },
  씨티카드: { note: '씨티카드는 로그인할 때 휴대폰 문자 인증을 요청할 수 있어요.' },
}

// 서버(POST /api/finance/connections/)가 받는 추가 칸은 카드번호 · 카드 비밀번호뿐 (finance-connect.md 10/27 결정 3)
// 다른 칸(본인인증 · 출금 계좌)이 꼭 필요한 기관은 NOT_SUPPORTED 로 뺐다 (11/13)
export const SERVER_FIELDS = { cardNo: 'cardNo', cardPw4: 'cardPassword', cardPw2: 'cardPassword', cardPw: 'cardPassword' }
// 다시 불러올 때 받는 카드 비밀번호: 칸 이름 뒤 안내 · 검사 (현대 4자리, 하나 · 국민(카드 소지 확인) 앞 2자리, 11/14)
const CARD_PW = { '0302': [' 4자리', 4], '0313': [' 앞 2자리', 2], '0301': [' 앞 2자리', 2] }
export const cardPw = (code) => {
  const [hint, digits] = CARD_PW[code] ?? [' (2자리 또는 4자리)', null]
  return { hint, maxLength: digits ?? 4, ok: (value) => (digits ? new RegExp(`^\\d{${digits}}$`) : /^(\d{2}|\d{4})$/).test(value) }
}
// 서버가 카드 값을 더 요구하면(fields 에 cardNo · cardPassword) 다시 받을 칸
export const CARD_RECHECK = { fields: ['cardNo', 'cardPw'], label: '카드 확인', sub: '카드번호 · 카드 비밀번호가 필요해요', message: '카드사에서 카드번호와 카드 비밀번호를 요청했어요.' }

// 연결할 수 있는 기관 (CODEF 개발가이드 '은행 개요'·'카드 개요' · 상세 명세, 2026-11-13 확인. 서버 finance/institutions.py 와 같게)
//   label: 칩에 쓰는 짧은 이름 / name: 화면·연결에 쓰는 이름 / code: CODEF 기관코드
//   popular: 처음부터 보이는 칩 (나머지는 "다른 ○○ 보기"로 펼침). 은행 · 카드사 모두 지원하는 곳은 처음부터 다 보인다 (11/14)
//   lock: 비밀번호를 몇 번 틀리면 잠기는지 (모르면 null)
const bank = (label, code, popular, lock = null, name = `${label}은행`) => ({ label, name, code, popular, lock })
export const BANKS = [
  bank('신한', '0088', true, 5), bank('국민', '0004', true, 5), bank('하나', '0081', true, 5),
  bank('농협', '0011', true, 5), bank('기업', '0003', true, 5),
  bank('SC제일', '0023', true), bank('씨티', '0027', true, 5), bank('새마을', '0045', true, 5, '새마을금고'),
  bank('경남', '0039', true, 3), bank('전북', '0037', true, 5),
]

const card = (label, code, popular, lock = null) => ({ label, name: label, code, popular, lock })
export const CARDS = [
  card('신한카드', '0306', true, 5), card('국민카드', '0301', true), card('삼성카드', '0303', true, 5),
  card('현대카드', '0302', true), card('우리카드', '0309', true, 5), card('하나카드', '0313', true),
  card('NH카드', '0304', true, 5), card('BC카드', '0305', true),
  card('씨티카드', '0307', true), card('전북카드', '0315', true, 5),
]

// 아직 연결할 수 없는 곳 (11/13 결정: 공동인증서 · 휴대폰 본인인증 · 출금 계좌 확인이 필요한 곳, 서비스 종료, CODEF 목록에 없음)
//   은행: "준비 중인 은행 보기"를 펼치면 누를 수 없는 칩으로 / 카드사: 문장으로
export const NOT_SUPPORTED = {
  bank: ['우리', '카카오뱅크', '토스뱅크', 'K뱅크', '우체국', '대구', '부산', '광주', '제주', '산업', '수협', '신협'],
  card: ['롯데카드', '수협카드', '광주카드', '제주카드'],
}

const ALL = Object.fromEntries([...BANKS, ...CARDS].map((i) => [i.name, i]))
export const instInfo = (name) => ALL[name] ?? { name, lock: null }
