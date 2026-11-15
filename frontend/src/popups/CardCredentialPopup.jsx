// 팝업: 카드 값 넣고 다시 불러오기 — 홈 새로고침에서 현대카드처럼 매번 카드번호 · 카드 비밀번호가 필요한 카드사 (11/9)
//   전에는 '인증 실패'만 띄우고 연결 관리로 보내서 불편했다 → 그 자리에서 받아 POST /api/finance/sync/ 를 그 연결만 한 번 더
// props: item = 동기화 결과 한 항목 { connectionId, organizationCode, organizationName }, onDone(result) = 불러오기 끝난 뒤 (홈 새로고침)
// 카드번호 · 비밀번호는 이번 요청에만 쓰고 저장하지 않는다 (보낸 뒤 바로 지움)
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { needsCardValues, runFinanceSync, syncLines } from '../utils/financeSync.js'
import { toast } from '../utils/toast.js'
import CardNoInput from '../components/CardNoInput.jsx'
import { cardPw } from '../utils/institutions.js'

export default function CardCredentialPopup({ item, onDone }) {
  const { closePopup } = usePopup()
  const [form, setForm] = useState({ cardNo: '', cardPassword: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pw = cardPw(item.organizationCode)  // 현대 4자리 · 하나 · 국민 앞 2자리 (CardSyncPanel 과 같음)

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    if (!/^\d{14,19}$/.test(form.cardNo) || !pw.ok(form.cardPassword)) {
      setError('카드번호와 카드 비밀번호를 확인해 주세요.')
      return
    }
    setBusy(true); setError('')
    const credential = { connectionId: item.connectionId, ...form }
    setForm({ cardNo: '', cardPassword: '' })
    try {
      const result = await runFinanceSync({ connectionIds: [item.connectionId], cardCredentials: [credential] })
      const row = result.items?.find((r) => r.connectionId === item.connectionId)
      // 작업이 성공해도 기관별로는 실패 · 건너뜀일 수 있다 → 이 카드사가 succeeded 일 때만 닫고 성공 안내 (PR #101 리뷰, CardSyncPanel 과 같게)
      if (!row) { setError('해당 카드사의 조회 결과를 확인하지 못했어요. 다시 시도해 주세요.'); return }
      if (needsCardValues(row)) { setError(Object.values(row.error.fields).flat()[0] ?? '카드번호와 카드 비밀번호를 확인해 주세요.'); return }
      if (row.status !== 'succeeded') { setError(syncLines({ items: [row] })[0]?.text ?? '카드 내역을 불러오지 못했어요.'); return }
      closePopup()
      toast('카드 내역을 불러왔어요')
      onDone?.(result)  // 닫은 뒤에 불러야 onDone 이 다음 카드사 팝업을 열 수 있다
    } catch (err) {
      setError(err.message)
    } finally { setBusy(false) }
  }
  const digits = (key, max) => (e) => setForm((f) => ({ ...f, [key]: e.target.value.replace(/\D/g, '').slice(0, max) }))

  return (
    <Popup label={`${item.organizationName} 카드 값 입력`}>
      <div className="mc-eyebrow">{item.organizationName}</div>
      <h2>카드 정보를 넣어 주세요</h2>
      <p className="mc-muted">{item.organizationName}는 불러올 때마다 카드번호와 카드 비밀번호가 필요해요.<br />이번 요청에만 쓰고 저장하지 않아요.</p>
      <form onSubmit={submit} noValidate>
        <label className="mc-field">카드번호<CardNoInput value={form.cardNo} onChange={(cardNo) => setForm((f) => ({ ...f, cardNo }))} /></label>
        <label className="mc-field">카드 비밀번호{pw.hint}<input autoComplete="off" type="password" inputMode="numeric" value={form.cardPassword} onChange={digits('cardPassword', pw.maxLength)} /></label>
        {error && <p className="mc-error" role="alert">{error}</p>}
        <button type="submit" className="mc-primary" disabled={busy}>{busy ? '불러오는 중…' : '넣고 다시 불러오기'}</button>
      </form>
    </Popup>
  )
}
