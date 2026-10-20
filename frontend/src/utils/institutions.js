// 기관별로 로그인 때 더 받아야 하는 값 (CODEF 개발가이드 기준, 2026-10-20 확인)
// 진짜 연동 때도 그대로 쓰는 규칙이라 mock 이 아니다. 기관이 늘면 여기에만 추가한다
//   fields: 아이디·비밀번호 말고 더 받을 칸 / note: 로그인 전에 보여 줄 안내 / warn: 주의 문구

export const FIELD = {
  cardNo: { label: '카드번호', placeholder: '숫자만 16자리', inputMode: 'numeric', maxLength: 16 },
  cardPw4: { label: '카드 비밀번호 4자리', type: 'password', inputMode: 'numeric', maxLength: 4 },
  cardPw2: { label: '카드 비밀번호 앞 2자리', type: 'password', inputMode: 'numeric', maxLength: 2 },
  userName: { label: '이름', placeholder: '홍길동' },
  identity: { label: '생년월일 6자리 + 뒷자리 첫 숫자', placeholder: '9903151', inputMode: 'numeric', maxLength: 7 },
  telecom: { label: '통신사', options: ['SKT', 'KT', 'LG U+', '알뜰폰(SKT)', '알뜰폰(KT)', '알뜰폰(LG U+)'] },
  phoneNo: { label: '휴대폰 번호', placeholder: '01012345678', inputMode: 'numeric', maxLength: 11 },
}

export const EXTRA = {
  현대카드: { fields: ['cardNo', 'cardPw4'], warn: '카드 비밀번호를 3번 틀리면 카드사 계정이 잠겨요.' },
  하나카드: { fields: ['cardNo', 'cardPw2'] },
  롯데카드: { fields: ['userName', 'identity', 'telecom', 'phoneNo'], note: '롯데카드는 로그인할 때 휴대폰 문자 인증을 한 번 더 해요.' },
  국민카드: { note: '카드사에서 카드 소지 확인을 요청하면 카드번호를 한 번 더 물어볼 수 있어요.' },
  농협은행: { note: 'e농협 홈페이지에서 "조회계좌"로 등록한 계좌만 불러올 수 있어요.' },
}

// CODEF 지원 기관 목록 (개발가이드 '은행 개요'·'카드 개요' 기준, 2026-10-20 확인: 은행 20곳 · 카드사 14곳)
//   label: 칩에 쓰는 짧은 이름 / name: 화면·연결에 쓰는 이름 / code: CODEF 기관코드
//   popular: 처음부터 보이는 칩 (나머지는 "다른 ○○ 보기"로 펼침)
//   idLogin: false 면 공동인증서로만 연결됨 / lock: 비밀번호를 몇 번 틀리면 잠기는지 (모르면 null)
const bank = (label, code, popular, idLogin = true, lock = null, name = `${label}은행`) => ({ label, name, code, popular, idLogin, lock })
export const BANKS = [
  bank('신한', '0088', true, true, 5), bank('국민', '0004', true, true, 5), bank('우리', '0020', true, true, 3),
  bank('하나', '0081', true, true, 5), bank('농협', '0011', true, true, 5), bank('기업', '0003', true, true, 5),
  bank('K뱅크', '0089', true, true, null, 'K뱅크'),
  bank('SC제일', '0023', false), bank('씨티', '0027', false, true, 5), bank('산업', '0002', false, false),
  bank('수협', '0007', false, false), bank('우체국', '0071', false, true, null, '우체국'),
  bank('새마을금고', '0045', false, true, 5, '새마을금고'), bank('신협', '0048', false, false, null, '신협'),
  bank('대구', '0031', false, true, 3), bank('부산', '0032', false, false), bank('경남', '0039', false, true, 3),
  bank('광주', '0034', false, false), bank('전북', '0037', false, true, 5), bank('제주', '0035', false, false),
]

const card = (label, code, popular, idLogin = true, lock = null) => ({ label, name: label, code, popular, idLogin, lock })
export const CARDS = [
  card('신한카드', '0306', true, true, 5), card('국민카드', '0301', true), card('삼성카드', '0303', true, true, 5),
  card('현대카드', '0302', true), card('롯데카드', '0311', true, true, 5), card('우리카드', '0309', true, true, 5),
  card('하나카드', '0313', true), card('NH카드', '0304', true, true, 5), card('BC카드', '0305', true),
  card('씨티카드', '0307', false), card('수협카드', '0320', false, false), card('광주카드', '0316', false, false),
  card('전북카드', '0315', false, true, 5), card('제주카드', '0321', false, false),
]

// 자주 묻지만 CODEF 목록에 없는 곳 (2026-10-20 기준)
export const NOT_SUPPORTED = ['카카오뱅크', '토스뱅크']

const ALL = Object.fromEntries([...BANKS, ...CARDS].map((i) => [i.name, i]))
export const instInfo = (name) => ALL[name] ?? { name, idLogin: true, lock: null }
