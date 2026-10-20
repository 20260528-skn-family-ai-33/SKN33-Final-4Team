// 임시 기관 연결 — 백엔드(CODEF 계정 등록 API)가 생기기 전까지 화면 흐름만 보여 준다
// 아무 정보도 밖으로 보내지 않는다. 진짜 연동 때 이 파일 대신 API 를 부른다
//   비밀번호에 'wrong' → 연결 실패 (CF-12100) / 롯데카드·씨티 → 문자 인증 / 국민카드 → 카드 소지 확인(CF-12108) 한 번
//   문자 인증번호 '000000' → 실패 / 그 외 → 성공

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

export async function mockConnect(inst, form) {
  await wait(900)
  if (form.password === 'wrong') return { status: 'fail', code: 'CF-12100', message: '아이디 또는 비밀번호가 맞지 않아요.' }
  if (inst === '국민카드' && !form.cardNo) return { status: 'recheck', code: 'CF-12108' }
  if (['롯데카드', '씨티은행', '씨티카드'].includes(inst)) return { status: 'sms', phoneHint: `${(form.phoneNo || '010').slice(0, 3)}-****-${(form.phoneNo || '').slice(-4) || '0000'}` }
  return { status: 'ok' }
}

export async function mockSms(code) {
  await wait(700)
  if (code === '000000') return { status: 'fail', code: 'CF-12801', message: '인증번호가 맞지 않아요.' }
  return { status: 'ok' }
}
