// 이미 있는 연결 다시 로그인 — POST /api/finance/connections/ (같은 연결 행을 되살림)
// 11/14: 씨티처럼 다시 연결할 때 문자 인증을 요구하면(sms_required) 그 자리에서 문자 인증 팝업을 띄우고 이어서 보낸다
//   (전에는 "계좌 연결 화면에서 다시 연결해 주세요"만 띄워서 화면을 타고 들어가야 했다)
// 연결 관리 · 카드 조회 칸 · 청구서 확인 창이 같이 쓴다. 로그인 값은 이 함수 안 메모리에만 두고 끝나면 버린다
import SmsAuthPopup from '../popups/SmsAuthPopup.jsx'
import { apiSend } from './api.js'
import { SERVER_FIELDS } from './institutions.js'

// 로그인 팝업 입력 → 서버가 받는 기관 한 줄
export function loginRow(connection, form) {
  const row = { businessType: connection.businessType ?? 'card', organizationCode: connection.organizationCode, loginId: form.id.trim(), password: form.password }
  for (const [key, value] of Object.entries(form)) if (SERVER_FIELDS[key] && value) row[SERVER_FIELDS[key]] = value
  return row
}

// 다시 연결 → 결과 한 기관 { status: connected · failed, syncStatus?, error? }. 오류도 던지지 않고 failed 로 돌려준다
//   beforeSms · afterSms: 문자 인증 팝업을 열기 직전 · 닫힌 뒤 (청구서 확인 창처럼 팝업 자리를 내준 쪽이 자기 창을 다시 열 때)
export async function reconnect(openPopup, connection, form, { beforeSms, afterSms } = {}) {
  let row = loginRow(connection, form)
  for (;;) {
    let item
    try {
      item = (await apiSend('/api/finance/connections/', 'POST', { institutions: [row] })).results?.[0]
    } catch (err) {
      return { status: 'failed', error: { message: Object.values(err.fields ?? {}).flat()[0] || err.message } }
    }
    if (item?.status !== 'sms_required') return item ?? { status: 'failed', error: { message: '다시 연결하지 못했어요.' } }
    // 인증번호가 틀리면 서버가 새 토큰과 함께 다시 sms_required + error 를 준다 → 그 문구를 팝업에 보여 주고 한 번 더
    beforeSms?.()
    const code = await new Promise((resolve) => openPopup(
      <SmsAuthPopup inst={connection.organizationName} phoneHint={item.smsAuth?.phoneHint} notice={item.error?.message}
        onSubmit={resolve} onCancel={() => resolve(null)} />,
    ))
    afterSms?.()  // await 뒤라서 문자 인증 팝업이 스스로 닫힌(closePopup) 다음에 실행된다. 다음 요청보다 먼저
    if (!code) return { status: 'failed', error: { message: '문자 인증을 취소했어요.' } }
    row = { ...row, smsAuth: { authToken: item.smsAuth.authToken, code } }
  }
}
