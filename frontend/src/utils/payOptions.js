// 결제 수단 후보 목록의 상태 · 안내 문구 — 화면 없이 테스트하려고 api.js 를 import 하지 않는다 (node --test src/utils/payOptions.test.js)

// useApi 결과 → loading · ready · consent(조회 동의 필요) · login(로그인 풀림) · error(서버 · 네트워크)
export function payStatus(data, error, retrying = false) {
  if (retrying || (!data && !error)) return 'loading'
  if (!error) return 'ready'
  if (error.code === 'consent_required') return 'consent'
  if (error.code === 'not_authenticated') return 'login'
  return 'error'
}

// 상태 안내 한 줄 (받았고 고를 수 있는 게 있으면 null). noun = '계좌' · '카드'
export function payOptionsNote(status, count, noun) {
  if (status === 'loading') return `${noun} 목록을 불러오는 중…`
  if (status === 'consent') return `${noun} 조회 동의가 필요해요. 금융 연결에서 동의하거나 '모름'을 골라 저장할 수 있어요.`
  if (status === 'login') return '로그인이 풀렸어요. 다시 로그인한 뒤 골라 주세요.'
  if (status === 'error') return `${noun} 목록을 불러오지 못했어요.`
  return count === 0 ? `고를 수 있는 ${noun}가 없어요. 금융 연결에서 관리할 ${noun}를 고르거나 '모름'을 골라 저장할 수 있어요.` : null
}
