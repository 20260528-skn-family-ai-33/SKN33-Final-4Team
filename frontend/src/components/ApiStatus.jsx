// 데이터를 기다리는 동안 / 실패했을 때 보여 주는 카드
// props: error = useApi 의 error (없으면 '불러오는 중'), onRetry = [다시 시도] 때 할 일 (선택, 없으면 화면 새로고침)
// 오류 종류는 docs/API_RULES.md 에러 표. 로그인 안 함은 403 + not_authenticated 라서 상태가 아니라 code 로 가른다
import { Link } from 'react-router'
import { PATHS } from '../routes.js'

// 서버 문장은 {"error": {code, message}} 로 온 것만 쓴다 — HTML · 스택 같은 원문은 화면에 안 낸다
const serverText = (error, fallback) => (error.code && error.message ? error.message : fallback)

function describeError(error) {
  const { status, code } = error
  if (status === 401 || code === 'not_authenticated') {
    return { title: '로그인이 필요해요', text: '로그인이 풀렸어요. 다시 로그인해 주세요.', to: [PATHS.login, '로그인하기'] }
  }
  if (code === 'consent_required') {
    return { title: '조회 동의가 필요해요', text: '금융 연결에서 조회에 동의하면 볼 수 있어요.', to: [PATHS.connect, '금융 연결로 가기'] }
  }
  if (status === 403) return { title: '볼 수 없는 정보예요', text: serverText(error, '이 정보를 볼 권한이 없어요.') }
  if (status === 404) return { title: '찾을 수 없어요', text: '지워졌거나 더는 볼 수 없는 정보예요.' }
  if (status === 409) return { title: '지금은 보여 드릴 수 없어요', text: serverText(error, '정보가 바뀌었을 수 있어요. 다시 불러와 주세요.'), retry: true }
  if (status === 429) return { title: '요청이 많아요', text: '잠시 뒤에 다시 시도해 주세요.', retry: true }
  // 네트워크 끊김(status 없음) · 5xx · 그 밖
  return { title: '데이터를 못 불러왔어요', text: '일시적인 문제일 수 있어요. 다시 시도해 주세요.', retry: true }
}

export default function ApiStatus({ error, onRetry }) {
  if (!error) {
    return <div className="mc-card"><p className="mc-muted" role="status" style={{ margin: 0 }}>불러오는 중…</p></div>
  }
  const { title, text, to, retry } = describeError(error)
  return (
    <div className="mc-card" role="alert">
      <h2>{title}</h2>
      <p className="mc-muted">{text}</p>
      {to && <Link className="mc-secondary" to={to[0]}>{to[1]}</Link>}
      {retry && <button type="button" className="mc-secondary" onClick={onRetry ?? (() => window.location.reload())}>다시 시도</button>}
    </div>
  )
}
