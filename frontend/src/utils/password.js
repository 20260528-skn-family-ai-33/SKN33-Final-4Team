// 비밀번호 규칙 (10/22 결정기록 X): 8~25자, 영문 · 숫자 · 특수문자 중 2가지 이상. 서버 users/serializers.py check_password_rule 과 같다
// 회원가입 · 비밀번호 재설정 화면이 같이 쓴다
export const PW_MIN = 8
export const PW_MAX = 25
// 영문 · 숫자 · 특수문자 중 몇 가지를 썼는지
const pwKinds = (p) => [/[A-Za-z]/, /[0-9]/, /[^A-Za-z0-9\s]/].filter((re) => re.test(p)).length
export const PW_RULES = [
  [`${PW_MIN}~${PW_MAX}자`, (p) => p.length >= PW_MIN && p.length <= PW_MAX],
  ['영문 · 숫자 · 특수문자 중 2가지 이상', (p) => pwKinds(p) >= 2],
]
export const pwOk = (p) => PW_RULES.every(([, test]) => test(p))
