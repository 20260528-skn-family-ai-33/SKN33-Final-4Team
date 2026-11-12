// 팝업(가운데 작은 창): 지난 카드값 미반영 → [청구서 확인하러 가기] (11/11)
// 창이 뜨면 먼저 청구서가 이미 들어와 있는지 보고(11/12, 홈 새로고침 등), 없으면 해당 카드사의 내역 · 청구서를 다시 불러온다 (POST /api/finance/sync/ → 작업 끝날 때까지 기다림).
// 끝나면 지킬 돈 설정(GET /api/budgets/protect/)의 cardNotes 를 다시 읽어, 이 카드 중 하나라도 first_bill_unavailable 이 사라졌으면
//   = 새 청구서가 나왔으면 → 창을 닫고 onFound (지킬 돈 설정으로 가서 다시 추천)
//   = 아직 그대로면 → 모든 카드사를 실제로 불러온 경우에만 "아직 조회되지 않았어요". 불러오지 못한 카드사가 있으면 그 이유와 [다시 시도] (PR #116 리뷰)
// 카드번호를 매번 묻는 카드사(현대카드 0302) · 다시 로그인이 필요한 연결은 자동으로 못 불러와서, 기존 카드사 조회 칸(CardSyncPanel)을 창 안에 보여 준다.
//   거기서 불러오기에 성공하면 바로 청구서를 확인한다 (입력 칸은 사라지고 결과로 넘어감). 실패하면 조회 칸에 이유가 남는다
//   카드사가 여러 곳이면 자동으로 되는 곳을 먼저 불러와 확인하고, 그래도 새 청구서가 없을 때만 남은 카드사를 조회 칸으로 보여 준다
//   직접 조회할 카드사가 여럿이면 끝낸 카드사(connectionId)를 세어, 새 청구서가 없어도 남은 카드사의 조회 칸은 그대로 둔다.
//   모든 카드사를 끝냈을 때만 '아직 없음' (PR #116 2차 리뷰). 다시 로그인으로 새 창을 열 때도 끝낸 카드사(done)를 넘겨 다시 묻지 않는다
// 다시 로그인(PR #116 리뷰): 로그인 팝업이 뜨면 팝업은 하나만 열려서 이 창은 닫힌다. 그래서 로그인 결과는 창 밖 함수(reconnectCard)가 받고,
//   새 확인 창이 그 결과(resume)를 이어받아 성공이면 다시 불러오기부터, 실패면 이유를 보여 준다
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import ConnectLoginPopup from './ConnectLoginPopup.jsx'
import Icon from '../components/Icon.jsx'
import CardSyncPanel from '../components/CardSyncPanel.jsx'
import { apiSend } from '../utils/api.js'
import { runFinanceSync, syncLines } from '../utils/financeSync.js'
import { SERVER_FIELDS } from '../utils/institutions.js'
import { PATHS } from '../routes.js'

const NOT_YET = 'first_bill_unavailable'
const ASK_CARD_VALUES = '0302'  // 현대카드: 조회마다 카드번호 · 카드 비밀번호 (CardSyncPanel 과 같은 기준)
const UNCHECKED = '조회 결과를 확인하지 못했어요. 다시 시도해 주세요.'  // 두 번 불러와도 결과에 없는 카드사

// 카드사 다시 로그인 (CardSyncPanel 의 다시 연결과 같은 요청). 창이 닫혀도 끝까지 가서 결과만 돌려준다
async function reconnectCard(connection, form) {
  const name = connection.organizationName
  const institution = { businessType: 'card', organizationCode: connection.organizationCode, loginId: form.id.trim(), password: form.password }
  for (const [key, value] of Object.entries(form)) if (SERVER_FIELDS[key] && value) institution[SERVER_FIELDS[key]] = value
  try {
    const result = await apiSend('/api/finance/connections/', 'POST', { institutions: [institution] })
    const item = result.results?.[0]
    if (item?.status === 'connected') return { ok: true }
    if (item?.status === 'sms_required') return { ok: false, needsAuth: true, name, text: '카드사 추가 인증이 필요해요. 금융 연결 화면에서 인증을 마쳐 주세요.' }
    return { ok: false, name, text: item?.error?.message || '카드사에 다시 연결하지 못했어요.' }
  } catch (err) {
    return { ok: false, name, text: Object.values(err.fields ?? {}).flat()[0] || err.message }
  }
}

// props: cardIds = 청구서가 아직 안 보이는 카드, onFound = 새 청구서가 있을 때 (창은 닫고 부른다)
//        resume = 다시 로그인 결과 Promise ({ ok, name, text, needsAuth }) — 로그인 팝업 뒤에 새로 연 창만 받는다
//        done = 이 흐름에서 이미 직접 조회를 끝낸 연결 ID (다시 로그인 뒤 새 창이 다시 묻지 않게)
export default function BillCheckDialog({ cardIds = [], onFound, resume = null, done = [] }) {
  const { openPopup, closePopup } = usePopup()
  const navigate = useNavigate()
  // reconnecting 다시 연결 중 · syncing 불러오는 중 · checking 청구서 확인 중 · manual 카드사 확인 필요 · none 아직 없음 · error 실패
  const [phase, setPhase] = useState(resume ? 'reconnecting' : 'syncing')
  const [message, setMessage] = useState('')
  const [failedLines, setFailedLines] = useState([])  // 불러오지 못한 카드사 [{ name, text }] — 다음 단계까지 남긴다
  const failedRef = useRef([])
  const [needsAuth, setNeedsAuth] = useState(false)
  const [manualCardIds, setManualCardIds] = useState([])  // 직접 조회할 카드사의 카드 (아직 안 끝낸 카드사만)
  const manualRef = useRef([])  // 직접 조회할 연결 목록
  const doneRef = useRef(new Set(done))  // 직접 조회를 끝낸 연결 ID
  const started = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const rememberFailed = (lines) => {
    failedRef.current = lines
    setFailedLines(lines)
  }

  // 불러온 뒤 청구서가 새로 보이는지: 지킬 돈 설정의 cardNotes 는 읽을 때마다 새로 계산된다
  const hasNewBill = async () => {
    const data = await apiSend('/api/budgets/protect/', 'GET')
    const still = new Set((data.cardNotes ?? []).filter((n) => n.reason === NOT_YET).map((n) => n.cardId))
    return cardIds.some((id) => !still.has(id))
  }
  const found = () => {
    closePopup()
    onFound()
  }
  // 있으면 창을 닫고 onFound (true). 없으면 stay 가 아닐 때: 못 불러온 카드사가 있으면 실패 안내, 다 불러왔으면 '아직 없음' (false)
  const check = async ({ stay = false } = {}) => {
    setPhase('checking')
    const ready = await hasNewBill()
    if (!mounted.current) return true
    if (ready) {
      found()
      return true
    }
    if (!stay) setPhase(failedRef.current.length ? 'error' : 'none')
    return false
  }

  const fail = (err) => {
    if (!mounted.current) return
    setMessage(err.message || '청구서를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.')
    setPhase('error')
  }

  // 이 카드사들만 불러온다. 다른 불러오기(홈 새로고침 · 자동 불러오기)가 돌고 있으면 서버는 대상과 상관없이 그 작업을 돌려줘서
  // (finance/api_sync.sync · tasks.start_or_reuse) 결과에 일부 카드사만 있을 수 있다 → 빠진 카드사만 한 번 더 부른다.
  // 그래도 없는 카드사는 '조회 못 함'으로 센다 (아직 없음과 구분)
  const syncConnections = async (targets) => {
    const ids = targets.map((c) => c.connectionId)
    const rows = new Map()
    const collect = (result) => { for (const item of result.items ?? []) if (ids.includes(item.connectionId)) rows.set(item.connectionId, item) }
    collect(await runFinanceSync({ connectionIds: ids }))
    const missing = ids.filter((id) => !rows.has(id))
    if (missing.length) collect(await runFinanceSync({ connectionIds: missing }))
    const failed = targets
      .filter((c) => rows.get(c.connectionId)?.status !== 'succeeded')  // 건너뜀(동의 없음 등)도 청구서를 확인한 게 아니라서 함께 센다
      .map((c) => rows.get(c.connectionId) ?? { organizationName: c.organizationName, status: 'failed', error: { message: UNCHECKED } })
    return { ok: ids.length - failed.length, failed }
  }

  const run = async () => {
    setPhase('syncing')
    setMessage('')
    setNeedsAuth(false)
    rememberFailed([])
    try {
      // 11/12: 홈 새로고침 등으로 이미 청구서가 들어와 있으면 다시 불러오지 않고 바로 넘어간다
      if (await hasNewBill()) {
        if (mounted.current) found()
        return
      }
      const { items = [] } = await apiSend('/api/finance/connections/', 'GET')
      const targets = items.filter((c) => c.businessType === 'card' && ['connected', 'relogin'].includes(c.status)
        && c.cardIds?.some((id) => cardIds.includes(id)))
      if (!targets.length) throw new Error('조회할 카드사 연결을 찾지 못했어요. 카드 연결 상태를 확인해 주세요.')
      // 카드사가 여러 곳이면 자동으로 되는 곳은 먼저 불러오고, 카드번호 · 다시 로그인이 필요한 곳만 조회 칸으로 남긴다
      const asks = (c) => c.status === 'relogin' || c.organizationCode === ASK_CARD_VALUES
      const auto = targets.filter((c) => !asks(c))
      const manual = targets.filter((c) => asks(c) && !doneRef.current.has(c.connectionId))
      if (auto.length) {
        const { ok, failed } = await syncConnections(auto)
        if (!mounted.current) return
        rememberFailed(syncLines({ items: failed }))
        // 자동으로 불러올 곳이 모두 실패했고 직접 조회할 곳도 없으면 청구서를 볼 수 없으니 바로 실패 안내
        if (!ok && !manual.length) {
          setPhase('error')
          return
        }
      }
      if (!mounted.current) return
      if (await check({ stay: manual.length > 0 })) return
      if (manual.length && mounted.current) {
        manualRef.current = manual
        showManual()
      }
    } catch (err) {
      fail(err)
    }
  }

  // 아직 끝내지 않은 카드사의 조회 칸만 보여 준다
  const manualLeft = () => manualRef.current.filter((c) => !doneRef.current.has(c.connectionId))
  const showManual = () => {
    setManualCardIds(manualLeft().flatMap((c) => c.cardIds ?? []).filter((id) => cardIds.includes(id)))
    setPhase('manual')
  }
  // 조회 칸에서 한 카드사를 불러오면: 끝낸 카드사로 세고 청구서 확인. 새 청구서가 없고 남은 카드사가 있으면 그 조회 칸으로 돌아간다
  const manualDone = async (result) => {
    if (!result?.ok) return  // 실패는 조회 칸에 이유가 남는다
    doneRef.current.add(result.connectionId)
    const left = manualLeft().length
    if (await check({ stay: left > 0 })) return
    if (left > 0 && mounted.current) showManual()
  }

  // 다시 로그인: 로그인 팝업이 이 창을 대신하므로, 결과는 reconnectCard 가 받아 두고 로그인 팝업이 닫힌 뒤 새 확인 창에 넘긴다
  const relogin = (connection) => openPopup(<ConnectLoginPopup inst={connection.organizationName} onSubmit={(form) => {
    const pending = reconnectCard(connection, form)
    // 로그인 팝업은 onSubmit 뒤에 스스로 닫힌다. 이미 직접 조회한 카드사는 새 창에 넘겨 다시 묻지 않는다
    setTimeout(() => openPopup(<BillCheckDialog cardIds={cardIds} onFound={onFound} resume={pending} done={[...doneRef.current]} />))
  }} />)

  useEffect(() => {
    if (started.current) return  // 개발 모드에서 두 번 도는 것 막기 (불러오기는 한 번만)
    started.current = true
    // 다시 로그인으로 연 창이면 그 결과를 기다리고, 아니면 바로 시작한다 (then 안에서 상태를 바꿔 첫 화면을 그린 뒤에 진행)
    ;(resume ?? Promise.resolve({ ok: true })).then((result) => {
      if (!mounted.current) return
      if (result.ok) run()  // 다시 연결됐으면 처음부터 (이번엔 자동으로 불러올 수 있는 연결)
      else {
        rememberFailed([{ name: result.name, text: result.text }])  // 불러오기 실패와 같은 모양 (카드사 이름 굵게)
        setNeedsAuth(Boolean(result.needsAuth))
        setPhase('error')
      }
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps -- 창이 뜰 때 한 번만

  // 실패 안내는 빨간 카드 안에 (홈 '확인 필요' 띠와 같은 빨간 테두리 · 연한 배경)
  const failedList = failedLines.length > 0 && (
    <div className="mc-bill-failed" role="alert">
      {failedLines.map((l) => <p key={l.name}><strong>{l.name}</strong> · {l.text}</p>)}
    </div>
  )

  return (
    <Popup label="카드 청구서 확인" dialog>
      <div style={{ wordBreak: 'keep-all' }}>
        {phase === 'reconnecting' && <>
          <h2>카드사에 다시 연결하고 있어요</h2>
          <p className="mc-muted mc-bill-busy" role="status"><Icon name="Loader" size={16} />잠시만 기다려 주세요.</p>
        </>}

        {(phase === 'syncing' || phase === 'checking') && <>
          <h2>{phase === 'syncing' ? '카드사에서 청구서를 불러오고 있어요' : '새 청구서가 있는지 확인하고 있어요'}</h2>
          <p className="mc-muted mc-bill-busy" role="status"><Icon name="Loader" size={16} />{phase === 'syncing' ? '1분 정도 걸릴 수 있어요.' : '잠시만 기다려 주세요.'}</p>
          {phase === 'syncing' && <p className="mc-note" style={{ paddingBottom: 0 }}>창을 닫아도 불러오기는 계속돼요.</p>}
        </>}

        {phase === 'manual' && <>
          <h2>카드사 확인이 필요해요</h2>
          <p className="mc-muted">카드 내역을 불러오면 바로 청구서를 확인할게요.</p>
          {failedList}
          <div className="mc-bill-manual">
            <CardSyncPanel cardIds={manualCardIds} onRelogin={relogin} onChanged={(result) => { manualDone(result).catch(fail) }} />
          </div>
        </>}

        {phase === 'none' && <>
          <h2>아직 청구서가 조회되지 않았어요</h2>
          <p className="mc-muted" style={{ margin: '2px 0' }}>카드사에서 청구서가 나오면 다시 확인해 주세요.</p>
          <p className="mc-muted" style={{ margin: '2px 0' }}>그때까지는 지금 예산이 그대로 유지돼요.</p>
          <button type="button" className="mc-primary" onClick={closePopup}>확인</button>
        </>}

        {phase === 'error' && <>
          <h2>청구서를 불러오지 못했어요</h2>
          {message && <div className="mc-bill-failed" role="alert"><p style={{ whiteSpace: 'pre-line' }}>{message}</p></div>}
          {failedList}
          {needsAuth
            ? <button type="button" className="mc-primary" onClick={() => { closePopup(); navigate(PATHS.connect) }}>추가 인증하러 가기</button>
            : <button type="button" className="mc-primary" onClick={run}>다시 시도</button>}
          <button type="button" className="mc-secondary" onClick={closePopup}>닫기</button>
        </>}
      </div>
    </Popup>
  )
}
