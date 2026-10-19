// 장고 API 에서 데이터 받아오기
// 사용법: const { data, error } = useApi('/api/home/')
//   data  = 받아온 데이터 (받기 전에는 null)
//   error = 실패했을 때의 이유 (괜찮으면 null)
import { useEffect, useState } from 'react'

export function useApi(path) {
  const [state, setState] = useState({ data: null, error: null })

  // path 가 바뀔 때마다(예: 카테고리 필터를 바꿀 때) 다시 받아온다
  useEffect(() => {
    const ctrl = new AbortController()  // 화면을 떠나면 받던 걸 취소하는 리모컨
    fetch(path, { signal: ctrl.signal, headers: { Accept: 'application/json' } })
      .then((res) => {
        if (!res.ok) throw new Error(`서버 응답 ${res.status}`)
        return res.json()
      })
      .then((data) => setState({ data, error: null }))
      .catch((err) => {
        if (err.name !== 'AbortError') setState({ data: null, error: err })
      })
    return () => ctrl.abort()
  }, [path])

  return state
}
