// 장고 API 와 주고받기. 규칙은 docs/API_RULES.md
//
// 1) 읽기 (화면 데이터)
//    const { data, error } = useApi('/api/home/')
//      data  = 받아온 데이터 (받기 전에는 null)
//      error = 실패했을 때 (error.status · error.code · error.message — 화면 안내는 <ApiStatus error onRetry />)
//      loading = 이 주소의 결과를 기다리는 중 · refreshing = reload() 로 같은 주소를 다시 받는 중
//
// 2) 저장·수정·삭제 (POST · PATCH · PUT · DELETE)
//    try {
//      const saved = await apiSend('/api/budgets/', 'POST', { livingBudget: 1120000 })
//    } catch (err) {
//      err.message  → "입력값을 확인해 주세요."
//      err.fields   → { livingBudget: ['...'] }  칸별 에러 (있을 때만)
//      err.code     → 'invalid' · 'not_authenticated' · 'not_found' ...
//    }
import { useEffect, useRef, useState } from 'react'

// 응답을 읽고, 실패면 장고가 보낸 {"error": {...}} 를 Error 로 바꿔서 던진다
async function readJson(res) {
  const body = await res.json().catch(() => null)  // JSON 이 아니면 null
  if (!res.ok) {
    const err = new Error(body?.error?.message ?? `서버 응답 ${res.status}`)
    err.status = res.status
    err.code = body?.error?.code
    err.fields = body?.error?.fields
    throw err
  }
  return body
}

// reload() = 같은 주소를 다시 받아오기 (예: 거래 카테고리를 저장한 뒤 목록 새로고침)
// 주소가 바뀌면(필터 이동) 그 순간 이전 조회 결과를 버린다 → 새 결과가 올 때까지 data = null (R29)
//   A → B → A 로 돌아와도 예전 A 결과를 다시 쓰지 않고 새로 받는다
// 같은 주소 reload 는 받는 동안 data 를 그대로 두고 refreshing = true. 실패하면:
//   기본 = data 를 비우고 error (if (!data) 로 오류 카드를 띄우는 화면이 그대로 동작)
//   options.keepData = true = 지난 data 를 남기고 error — 이걸 고른 화면은 data 가 있어도 error 를 꼭 보여 줘야 한다
// 늦게 온 이전 요청의 응답 · 오류는 요청 번호로 버린다 (취소가 늦게 먹혀도). 취소는 오류로 안 보인다
export function useApi(path, { keepData = false } = {}) {
  const empty = { path, data: null, error: null, refreshing: false }
  const [state, setState] = useState(empty)
  const [version, setVersion] = useState(0)
  const latest = useRef(0)
  // 필터 이동: 그리기 도중 바로 비운다 (React 가 이 결과를 화면에 내기 전에 다시 그린다)
  if (state.path !== path) setState(empty)

  // path 가 바뀔 때마다(예: 카테고리 필터를 바꿀 때) 다시 받아온다
  useEffect(() => {
    const id = ++latest.current
    const ctrl = new AbortController()  // 화면을 떠나거나 주소가 바뀌면 받던 걸 취소하는 리모컨
    const mine = () => id === latest.current && !ctrl.signal.aborted
    fetch(path, { signal: ctrl.signal, headers: { Accept: 'application/json' } })
      .then(readJson)
      .then((data) => { if (mine()) setState({ path, data, error: null, refreshing: false }) })
      .catch((error) => {
        if (mine()) setState((s) => ({ path, data: keepData ? s.data : null, error, refreshing: false }))
      })
    return () => ctrl.abort()
  }, [path, version, keepData])

  const current = state.path === path ? state : empty
  return {
    data: current.data,
    error: current.error,
    loading: !current.data && !current.error,   // 이 주소의 결과(성공 · 실패)를 아직 못 받음
    refreshing: current.refreshing,             // 같은 주소를 다시 받는 중
    reload: () => {
      setState((s) => ({ ...s, refreshing: true }))
      setVersion((v) => v + 1)
    },
  }
}

// 장고가 준 csrftoken 쿠키 꺼내기 (없으면 /api/csrf/ 에서 받아 온다)
// 로그인한 상태에서 저장할 때, 다른 사이트가 몰래 보낸 요청이 아니라는 증표
function readCookie(name) {
  const hit = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null
}

// 쿠키 이름은 기본 'csrftoken'(장고 기본). 다른 백엔드 설정(예: 더미 서비스 8001)에 붙일 때만 VITE_CSRF_COOKIE 로 바꾼다
const CSRF_COOKIE = import.meta.env.VITE_CSRF_COOKIE || 'csrftoken'

async function csrfToken() {
  if (!readCookie(CSRF_COOKIE)) await fetch('/api/csrf/')
  return readCookie(CSRF_COOKIE) ?? ''
}

// 요청 번호 — 폼(팝업)을 열 때 하나 만들어 두고, 저장할 때 apiSend 에 같이 넘긴다 (10-21 C-10)
// 같은 폼에서 다시 보내면 같은 번호라서 서버가 한 번만 반영한다. 새 항목을 또 만들려면 폼을 다시 열어 새 번호를 받는다
// crypto.randomUUID 는 https · localhost 에서만 있어서(휴대폰으로 http://192.168… 접속하면 없음) 없으면 직접 만든다
export const newRequestKey = () =>
  crypto.randomUUID?.() ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('')

// options.requestKey = newRequestKey() 로 만든 번호 (새로 만들기 · 승인 요청에 넣는다. docs/API_RULES.md '같은 요청을 두 번 보내면')
export async function apiSend(path, method, body, options = {}) {
  const request = {
    method,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRFToken': await csrfToken(),
      ...(options.requestKey ? { 'Idempotency-Key': options.requestKey } : {}),
    },
  }
  if (body !== undefined) request.body = JSON.stringify(body)  // 보낼 내용 (DELETE 등은 없을 수 있음)
  return readJson(await fetch(path, request))
}
