import { useEffect, useRef, useState } from 'react'
import { Navigate } from 'react-router'
import { useApi } from '../utils/api.js'
import { runFinanceSync, syncLines } from '../utils/financeSync.js'
import { cardPw } from '../utils/institutions.js'
import { reconnect as reconnectWith } from '../utils/reconnect.jsx'
import CardNoInput from './CardNoInput.jsx'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import ConnectLoginPopup from '../popups/ConnectLoginPopup.jsx'

function CardConnection({ connection, onChanged, onRelogin }) {
  const { openPopup } = usePopup()
  const [credentials, setCredentials] = useState({ cardNo: '', cardPassword: '' })
  const [needCredentials, setNeedCredentials] = useState(connection.organizationCode === '0302')
  const [needLogin, setNeedLogin] = useState(connection.status === 'relogin')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [lines, setLines] = useState([])
  const working = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const name = connection.organizationName
  const pw = cardPw(connection.organizationCode)

  const refresh = async (event) => {
    event.preventDefault()
    if (working.current) return
    if (needCredentials && (!/^\d{14,19}$/.test(credentials.cardNo) || !pw.ok(credentials.cardPassword))) {
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
      // 11/11: 청구서 확인 창이 성공했을 때만 바로 확인하러 넘어간다. 건너뜀(skipped)은 불러온 게 아니라서 성공이 아님 (PR #116 리뷰)
      //   connectionId: 확인 창이 어느 카드사를 끝냈는지 센다
      onChanged({ ok: item.status === 'succeeded', connectionId: connection.connectionId })
    } catch (err) {
      if (mounted.current) setError(err.message)
    } finally {
      working.current = false
      if (mounted.current) setBusy(false)
    }
  }

  // onRelogin 이 있으면 다시 로그인을 부른 쪽에 맡긴다 (청구서 확인 창: 로그인 팝업이 창을 대신해서 여기서는 결과를 못 받음, PR #116 리뷰)
  const reconnect = onRelogin ? () => onRelogin(connection) : () => openPopup(<ConnectLoginPopup inst={name} onSubmit={async (form) => {
    if (working.current) return
    working.current = true
    setBusy(true)
    setError('')
    setLines([])
    try {
      // 문자 인증을 요구하면 그 자리에서 문자 인증 팝업 (utils/reconnect.jsx)
      const item = await reconnectWith(openPopup, { ...connection, businessType: 'card' }, form)
      if (!mounted.current) return
      if (item.status === 'connected') {
        setNeedLogin(false)
        setLines([{ name, ok: item.syncStatus !== 'failed', text: item.syncStatus === 'failed'
          ? '다시 연결했지만 내역을 가져오지 못했어요. 아래에서 다시 불러와 주세요.' : '다시 연결하고 카드 내역을 불러왔어요.' }])
      } else setError(item.error?.message || '카드사에 다시 연결하지 못했어요.')
      onChanged({ ok: item.status === 'connected' && item.syncStatus !== 'failed', connectionId: connection.connectionId })
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
        <label className="mc-field">카드 비밀번호{pw.hint}
          <input name="cardPassword" type="password" autoComplete="off" inputMode="numeric" maxLength={pw.maxLength} value={credentials.cardPassword} disabled={busy}
            onChange={(event) => setCredentials((previous) => ({ ...previous, cardPassword: event.target.value.replace(/\D/g, '') }))} />
        </label>
        <p className="mc-note">입력한 정보는 이번 조회에만 사용해요.</p>
      </>}
      <button type="submit" className="mc-secondary" disabled={busy}>{busy ? '불러오는 중…' : `${name} 내역 다시 불러오기`}</button>
    </form>}
    {lines.map((line) => <p key={line.name} className={line.ok ? 'mc-note' : 'mc-error'} role="status">{line.text}</p>)}
    {error && <p className="mc-error" role="alert">{error}</p>}
  </section>
}

export default function CardSyncPanel({ cardIds, onChanged, onRelogin }) {
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
  return <div>{connections.map((connection) => <CardConnection key={connection.connectionId} connection={connection} onRelogin={onRelogin} onChanged={(result) => {
    query.reload()
    onChanged(result)
  }} />)}</div>
}
