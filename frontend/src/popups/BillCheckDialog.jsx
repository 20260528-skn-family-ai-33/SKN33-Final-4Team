// 팝업(가운데 작은 창): 지난 카드값 미반영 → [청구서 확인하러 가기] (11/11)
// 창이 뜨면 먼저 청구서가 이미 들어와 있는지 보고(11/12, 홈 새로고침 등), 없으면 해당 카드사의 내역 · 청구서를 다시 불러온다 (POST /api/finance/sync/ → 작업 끝날 때까지 기다림).
// 끝나면 지킬 돈 설정(GET /api/budgets/protect/)의 cardNotes 를 다시 읽어, 이 카드 중 하나라도 first_bill_unavailable 이 사라졌으면
//   = 새 청구서가 나왔으면 → 창을 닫고 onFound (지킬 돈 설정으로 가서 다시 추천)
//   = 아직 그대로면 → "아직 조회되지 않았어요" 안내 후 [확인]으로 원래 화면에 남는다
// 카드번호를 매번 묻는 카드사(현대카드 0302) · 다시 로그인이 필요한 연결은 자동으로 못 불러와서, 기존 카드사 조회 칸(CardSyncPanel)을 창 안에 보여 준다.
//   거기서 불러오기에 성공하면 바로 청구서를 확인한다 (입력 칸은 사라지고 결과로 넘어감). 실패하면 조회 칸에 이유가 남는다
//   카드사가 여러 곳이면 자동으로 되는 곳을 먼저 불러와 확인하고, 그래도 새 청구서가 없을 때만 남은 카드사를 조회 칸으로 보여 준다
import { useEffect, useRef, useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import Icon from '../components/Icon.jsx'
import CardSyncPanel from '../components/CardSyncPanel.jsx'
import { apiSend } from '../utils/api.js'
import { runFinanceSync, syncLines } from '../utils/financeSync.js'

const NOT_YET = 'first_bill_unavailable'
const ASK_CARD_VALUES = '0302'  // 현대카드: 조회마다 카드번호 · 카드 비밀번호 (CardSyncPanel 과 같은 기준)

// props: cardIds = 청구서가 아직 안 보이는 카드, onFound = 새 청구서가 있을 때 (창은 닫고 부른다)
export default function BillCheckDialog({ cardIds = [], onFound }) {
  const { closePopup } = usePopup()
  // syncing 불러오는 중 · checking 청구서 확인 중 · manual 카드사 확인 필요 · none 아직 없음 · error 실패
  const [phase, setPhase] = useState('syncing')
  const [message, setMessage] = useState('')
  const [manualCardIds, setManualCardIds] = useState([])  // 직접 조회할 카드사의 카드
  const started = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  // 불러온 뒤 청구서가 새로 보이는지: 지킬 돈 설정의 cardNotes 는 읽을 때마다 새로 계산된다.
  // 있으면 창을 닫고 onFound (true). 없으면 stay 가 아닐 때 '아직 없음' (false)
  const hasNewBill = async () => {
    const data = await apiSend('/api/budgets/protect/', 'GET')
    const still = new Set((data.cardNotes ?? []).filter((n) => n.reason === NOT_YET).map((n) => n.cardId))
    return cardIds.some((id) => !still.has(id))
  }
  const found = () => {
    closePopup()
    onFound()
  }
  const check = async ({ stay = false } = {}) => {
    setPhase('checking')
    const ready = await hasNewBill()
    if (!mounted.current) return true
    if (ready) {
      found()
      return true
    }
    if (!stay) setPhase('none')
    return false
  }

  const fail = (err) => {
    if (!mounted.current) return
    setMessage(err.message || '청구서를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.')
    setPhase('error')
  }

  // 이 카드사들만 불러온다. 다른 불러오기(홈 새로고침 · 자동 불러오기)가 돌고 있으면 서버는 그 작업을 돌려줘서
  // (finance/api_sync.sync · tasks.start_or_reuse) 결과에 이 카드사가 없을 수 있다 → 그 작업이 끝난 뒤 한 번 더 부른다
  const syncConnections = async (ids) => {
    const mine = (result) => (result.items ?? []).filter((item) => ids.includes(item.connectionId))
    let rows = mine(await runFinanceSync({ connectionIds: ids }))
    if (!rows.length) rows = mine(await runFinanceSync({ connectionIds: ids }))
    return { ok: rows.filter((item) => item.status !== 'failed').length, failed: rows.filter((item) => item.status === 'failed') }
  }

  const run = async () => {
    setPhase('syncing')
    setMessage('')
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
      const manual = targets.filter(asks)
      if (auto.length) {
        const { ok, failed } = await syncConnections(auto.map((c) => c.connectionId))
        // 자동으로 불러올 곳이 모두 실패했고 직접 조회할 곳도 없으면 청구서를 볼 수 없으니 실패로
        if (!ok && !manual.length) {
          throw new Error(failed.length ? syncLines({ items: failed }).map((l) => `${l.name} · ${l.text}`).join('\n') : '카드사 내역을 불러오지 못했어요.')
        }
      }
      if (!mounted.current) return
      if (await check({ stay: manual.length > 0 })) return
      if (manual.length && mounted.current) {
        setManualCardIds(manual.flatMap((c) => c.cardIds ?? []).filter((id) => cardIds.includes(id)))
        setPhase('manual')
      }
    } catch (err) {
      fail(err)
    }
  }

  useEffect(() => {
    if (started.current) return  // 개발 모드에서 두 번 도는 것 막기 (불러오기는 한 번만)
    started.current = true
    run()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps -- 창이 뜰 때 한 번만

  return (
    <Popup label="카드 청구서 확인" dialog>
      <div style={{ wordBreak: 'keep-all' }}>
        {(phase === 'syncing' || phase === 'checking') && <>
          <h2>{phase === 'syncing' ? '카드사에서 청구서를 불러오고 있어요' : '새 청구서가 있는지 확인하고 있어요'}</h2>
          <p className="mc-muted mc-bill-busy" role="status"><Icon name="Loader" size={16} />{phase === 'syncing' ? '1분 정도 걸릴 수 있어요.' : '잠시만 기다려 주세요.'}</p>
          {phase === 'syncing' && <p className="mc-note" style={{ paddingBottom: 0 }}>창을 닫아도 불러오기는 계속돼요.</p>}
        </>}

        {phase === 'manual' && <>
          <h2>카드사 확인이 필요해요</h2>
          <p className="mc-muted">카드 내역을 불러오면 바로 청구서를 확인할게요.</p>
          <div className="mc-bill-manual">
            <CardSyncPanel cardIds={manualCardIds} onChanged={(result) => { if (result?.ok) check().catch(fail) }} />
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
          <p className="mc-error" role="alert" style={{ whiteSpace: 'pre-line' }}>{message}</p>
          <button type="button" className="mc-primary" onClick={run}>다시 시도</button>
          <button type="button" className="mc-secondary" onClick={closePopup}>닫기</button>
        </>}
      </div>
    </Popup>
  )
}
