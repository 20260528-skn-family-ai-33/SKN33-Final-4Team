// 장고 API 와 주고받기. 규칙은 docs/API_RULES.md
//
// 1) 읽기 (화면 데이터)
//    const { data, error } = useApi('/api/home/')
//      data  = 받아온 데이터 (받기 전에는 null)
//      error = 실패했을 때 (error.message = 화면에 보여 줄 문장)
//
// 2) 저장·수정·삭제 (POST · PATCH · PUT · DELETE)
//    try {
//      const saved = await apiSend('/api/budgets/', 'POST', { livingBudget: 1120000 })
//    } catch (err) {
//      err.message  → "입력값을 확인해 주세요."
//      err.fields   → { livingBudget: ['...'] }  칸별 에러 (있을 때만)
//      err.code     → 'invalid' · 'not_authenticated' · 'not_found' ...
//    }
import { useEffect, useState } from 'react'

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

export function useApi(path) {
  const [state, setState] = useState({ data: null, error: null })

  // path 가 바뀔 때마다(예: 카테고리 필터를 바꿀 때) 다시 받아온다
  useEffect(() => {
    const ctrl = new AbortController()  // 화면을 떠나면 받던 걸 취소하는 리모컨
    fetch(path, { signal: ctrl.signal, headers: { Accept: 'application/json' } })
      .then(readJson)
      .then((data) => setState({ data, error: null }))
      .catch((err) => {
        if (err.name !== 'AbortError') setState({ data: null, error: err })
      })
    return () => ctrl.abort()
  }, [path])

  return state
}

// 장고가 준 csrftoken 쿠키 꺼내기 (없으면 /api/csrf/ 에서 받아 온다)
// 로그인한 상태에서 저장할 때, 다른 사이트가 몰래 보낸 요청이 아니라는 증표
function readCookie(name) {
  const hit = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null
}

async function csrfToken() {
  if (!readCookie('csrftoken')) await fetch('/api/csrf/')
  return readCookie('csrftoken') ?? ''
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
