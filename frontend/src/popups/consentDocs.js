// 동의 문서는 서버(GET /api/consent-documents/)가 내려 준다. 원문 · 해시 기준은 backend/users/consent_documents.py 한 곳
// 체크 상태는 '목적:범위:버전'으로 기억한다 → 문서가 새 버전으로 바뀌면 이전 버전 체크가 넘어가지 않는다
import { useState } from 'react'
import { useApi } from '../utils/api.js'

const keyOf = (d) => `${d.purpose}:${d.productScope ?? ''}:${d.version}`

// pick = 이 화면에 보여 줄 문서 고르기 (예: 회원가입은 productScope 가 null 인 문서)
// docs 가 null 이면 아직 못 받았거나 실패(error) — 그동안은 동의를 받지 않는다
// initial = 처음부터 체크해 둘 키 목록 (뒤로 와서 다시 그릴 때, agreedKeys 로 받은 값). 버전이 바뀐 문서는 키가 달라 체크되지 않는다
export function useConsentDocs(pick, initial = []) {
  const { data, error, reload } = useApi('/api/consent-documents/')
  const [agreed, setAgreed] = useState(() => new Set(initial))
  const docs = data ? data.items.filter(pick) : null
  const isAgreed = (d) => agreed.has(keyOf(d))
  const setOne = (d, on) => setAgreed((s) => {
    const next = new Set(s)
    if (on) next.add(keyOf(d)); else next.delete(keyOf(d))
    return next
  })
  const setAll = (on) => setAgreed(new Set(on && docs ? docs.map(keyOf) : []))
  return {
    docs, error, isAgreed, setOne, setAll,
    agreedKeys: () => [...agreed],  // 지금 체크한 키 목록 (화면을 떠날 때 남겨 두기용)
    allAgreed: !!docs?.length && docs.every(isAgreed),
    // 문서를 다시 받기 (불러오기 실패 재시도 · 서버가 409 document_outdated 를 준 뒤) — 체크는 모두 지운다
    reload: () => { setAgreed(new Set()); reload() },
  }
}
