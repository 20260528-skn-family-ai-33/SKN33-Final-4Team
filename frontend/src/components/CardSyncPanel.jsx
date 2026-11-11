import { useEffect, useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router'
import { apiSend, useApi } from '../utils/api.js'
import { runFinanceSync, syncLines } from '../utils/financeSync.js'
import { SERVER_FIELDS } from '../utils/institutions.js'
import CardNoInput from './CardNoInput.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import ConnectLoginPopup from '../popups/ConnectLoginPopup.jsx'

function CardConnection({ connection, onChanged }) {
  const { openPopup } = usePopup()
  const navigate = useNavigate()
  const [credentials, setCredentials] = useState({ cardNo: '', cardPassword: '' })
  const [needCredentials, setNeedCredentials] = useState(connection.organizationCode === '0302')
  const [needLogin, setNeedLogin] = useState(connection.status === 'relogin')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [lines, setLines] = useState([])
  const [needsAdditionalAuth, setNeedsAdditionalAuth] = useState(false)
  const working = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const name = connection.organizationName

  const refresh = async (event) => {
    event.preventDefault()
    if (working.current) return
    const passwordPattern = connection.organizationCode === '0302' ? /^\d{4}$/ : /^(\d{2}|\d{4})$/
    if (needCredentials && (!/^\d{14,19}$/.test(credentials.cardNo) || !passwordPattern.test(credentials.cardPassword))) {
      setError('카드번호와 카드 비밀번호를 확인해 주세요.')
      return
    }
    working.current = true
    setBusy(true)
    setError('')
    setLines([])
    const body = { connectionIds: [connection.connectionId] }
    if (needCredentials) body.cardCredentials = [{ connectionId: connection.connectionId, ...credentials }]
    setCredentials({ cardNo: '', cardPassword: '' })
    try {
      const result = await runFinanceSync(body)
      if (!mounted.current) return
      const item = result.items?.find((row) => row.connectionId === connection.connectionId)
      if (!item) throw new Error('해당 카드사의 조회 결과를 확인하지 못했어요. 다시 시도해 주세요.')
      setLines(syncLines({ items: [item] }))
      if (item.error?.fields?.cardNo || item.error?.fields?.cardPassword) setNeedCredentials(true)
      if (item.error?.code === 'relogin_required') setNeedLogin(true)
      onChanged({ ok: item.status !== 'failed' })  // 11/11: 청구서 확인 창이 성공했을 때만 바로 확인하러 넘어간다
    } catch (err) {
      if (mounted.current) setError(err.message)
    } finally {
      working.current = false
      if (mounted.current) setBusy(false)
    }
  }

  const reconnect = () => openPopup(<ConnectLoginPopup inst={name} onSubmit={async (form) => {
    if (working.current) return
    working.current = true
    setBusy(true)
    setError('')
    setLines([])
    const institution = { businessType: 'card', organizationCode: connection.organizationCode, loginId: form.id.trim(), password: form.password }
    for (const [key, value] of Object.entries(form)) if (SERVER_FIELDS[key] && value) institution[SERVER_FIELDS[key]] = value
    try {
      const result = await apiSend('/api/finance/connections/', 'POST', { institutions: [institution] })
      if (!mounted.current) return
      const item = result.results?.[0]
      if (item?.status === 'connected') {
        setNeedLogin(false)
        setNeedsAdditionalAuth(false)
        setLines([{ name, ok: item.syncStatus !== 'failed', text: item.syncStatus === 'failed'
          ? '다시 연결했지만 내역을 가져오지 못했어요. 아래에서 다시 불러와 주세요.' : '다시 연결하고 카드 내역을 불러왔어요.' }])
      } else if (item?.status === 'sms_required') {
        setNeedsAdditionalAuth(true)
        setError('카드사 추가 인증이 필요해요. 금융 연결 화면에서 인증을 마쳐 주세요.')
      } else setError(item?.error?.message || '카드사에 다시 연결하지 못했어요.')
      onChanged({ ok: item?.status === 'connected' && item.syncStatus !== 'failed' })
    } catch (err) {
      if (mounted.current) setError(Object.values(err.fields ?? {}).flat()[0] || err.message)
    } finally {
      working.current = false
      if (mounted.current) setBusy(false)
    }
  }} />)

  return <section className="mc-card" aria-label={`${name} 내역 조회`}>
    <h3>{name}</h3>
    {needLogin ? <>
      <p className="mc-muted">카드사에 다시 로그인해야 해요.</p>
      <button type="button" className="mc-secondary" onClick={reconnect} disabled={busy}>{busy ? '연결하는 중…' : `${name} 다시 연결하기`}</button>
    </> : <form className="mc-form" onSubmit={refresh}>
      {needCredentials && <>
        <p className="mc-note">{name}에서 조회할 때 카드번호와 카드 비밀번호를 요청해요.</p>
        <label className="mc-field">카드번호
          <CardNoInput name="cardNo" value={credentials.cardNo} disabled={busy}
            onChange={(cardNo) => setCredentials((previous) => ({ ...previous, cardNo }))} />
        </label>
        <label className="mc-field">카드 비밀번호{connection.organizationCode === '0302' ? ' 4자리' : ' (2자리 또는 4자리)'}
          <input name="cardPassword" type="password" autoComplete="off" inputMode="numeric" maxLength={4} value={credentials.cardPassword} disabled={busy}
            onChange={(event) => setCredentials((previous) => ({ ...previous, cardPassword: event.target.value.replace(/\D/g, '') }))} />
        </label>
        <p className="mc-note">입력한 정보는 이번 조회에만 사용해요.</p>
      </>}
      <button type="submit" className="mc-secondary" disabled={busy}>{busy ? '불러오는 중…' : `${name} 내역 다시 불러오기`}</button>
    </form>}
    {lines.map((line) => <p key={line.name} className={line.ok ? 'mc-note' : 'mc-error'} role="status">{line.text}</p>)}
    {error && <p className="mc-error" role="alert">{error}</p>}
    {needsAdditionalAuth && <button type="button" className="mc-link" onClick={() => navigate(PATHS.connect)}>추가 인증하러 가기</button>}
  </section>
}

export default function CardSyncPanel({ cardIds, onChanged }) {
  const query = useApi('/api/finance/connections/')
  if (query.error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  if (query.error) return <div>
    <p className="mc-error" role="alert">{query.error.status ? query.error.message : '카드사 연결 정보를 불러오지 못했어요.'}</p>
    <button type="button" className="mc-link" onClick={query.reload}>연결 정보 다시 확인</button>
  </div>
  if (!query.data) return <p role="status">조회가 필요한 카드사를 확인하는 중이에요.</p>
  const connections = (query.data.items ?? []).filter((connection) => connection.businessType === 'card'
    && ['connected', 'relogin'].includes(connection.status)
    && connection.cardIds?.some((id) => cardIds.includes(id)))
  if (!connections.length) return <p role="alert">조회할 카드사 연결을 찾지 못했어요. 카드 연결 상태를 확인해 주세요.</p>
  return <div>{connections.map((connection) => <CardConnection key={connection.connectionId} connection={connection} onChanged={(result) => {
    query.reload()
    onChanged(result)
  }} />)}</div>
}
