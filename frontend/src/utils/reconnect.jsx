// 이미 있는 연결 다시 로그인 — POST /api/finance/connections/ (같은 연결 행을 되살림)
// 11/14: 씨티처럼 다시 연결할 때 문자 인증을 요구하면(sms_required) 그 자리에서 문자 인증 팝업을 띄우고 이어서 보낸다
//   (전에는 "계좌 연결 화면에서 다시 연결해 주세요"만 띄워서 화면을 타고 들어가야 했다)
//   국민카드가 카드 소지 확인(CF-12108)을 요구하면 400 fields(cardNo · cardPassword)가 온다 → 카드 칸을 붙인 로그인 팝업을 다시 띄우고 이어서 보낸다 (PR #122 리뷰)
// 연결 관리 · 카드 조회 칸 · 청구서 확인 창이 같이 쓴다. 로그인 값은 이 함수 안 메모리에만 두고 끝나면 버린다
import ConnectLoginPopup from '../popups/ConnectLoginPopup.jsx'
import SmsAuthPopup from '../popups/SmsAuthPopup.jsx'
import { apiSend } from './api.js'
import { CARD_RECHECK, SERVER_FIELDS } from './institutions.js'

// 로그인 팝업 입력 → 서버가 받는 기관 한 줄
export function loginRow(connection, form) {
  const row = { businessType: connection.businessType ?? 'card', organizationCode: connection.organizationCode, loginId: form.id.trim(), password: form.password }
  for (const [key, value] of Object.entries(form)) if (SERVER_FIELDS[key] && value) row[SERVER_FIELDS[key]] = value
  return row
}

// 다시 연결 → 결과 한 기관 { status: connected · failed, syncStatus?, error? }. 오류도 던지지 않고 failed 로 돌려준다
//   beforePopup · afterPopup: 문자 인증 · 카드 확인 팝업을 열기 직전 · 닫힌 뒤 (청구서 확인 창처럼 팝업 자리를 내준 쪽이 자기 창을 다시 열 때)
export async function reconnect(openPopup, connection, form, { beforePopup, afterPopup } = {}) {
  let row = loginRow(connection, form)
  // 팝업 하나를 띄우고 답(취소면 null)을 기다린다. 앞뒤로 부른 쪽이 자기 창을 비키고 · 다시 연다
  const ask = async (render) => {
    beforePopup?.()
    const answer = await new Promise((resolve) => openPopup(render(resolve)))
    afterPopup?.()  // await 뒤라서 팝업이 스스로 닫힌(closePopup) 다음에 실행된다. 다음 요청보다 먼저
    return answer
  }
  for (;;) {
    let item
    try {
      item = (await apiSend('/api/finance/connections/', 'POST', { institutions: [row] })).results?.[0]
    } catch (err) {
      const message = Object.values(err.fields ?? {}).flat()[0] || err.message
      if (!/cardNo|cardPassword/.test(Object.keys(err.fields ?? {}).join(' '))) return { status: 'failed', error: { message } }
      const next = await ask((resolve) => (
        <ConnectLoginPopup key={`card-${connection.connectionId}`} inst={connection.organizationName} recheck={CARD_RECHECK} onSubmit={resolve} onCancel={() => resolve(null)} />
      ))
      if (!next) return { status: 'failed', error: { message: '카드 확인을 취소했어요.' } }
      row = loginRow(connection, next)
      continue
    }
    if (item?.status !== 'sms_required') return item ?? { status: 'failed', error: { message: '다시 연결하지 못했어요.' } }
    // 인증번호가 틀리면 서버가 새 토큰과 함께 다시 sms_required + error 를 준다 → 그 문구를 팝업에 보여 주고 한 번 더
    // key: 다른 기관 다시 연결이 팝업 자리를 덮으면 앞 팝업이 진짜로 닫혀(언마운트) 앞 연결이 취소로 끝나게
    const code = await ask((resolve) => (
      <SmsAuthPopup key={item.smsAuth?.authToken} inst={connection.organizationName} phoneHint={item.smsAuth?.phoneHint} notice={item.error?.message}
        onSubmit={resolve} onCancel={() => resolve(null)} />
    ))
    if (!code) return { status: 'failed', error: { message: '문자 인증을 취소했어요.' } }
    row = { ...row, smsAuth: { authToken: item.smsAuth.authToken, code } }
  }
}
