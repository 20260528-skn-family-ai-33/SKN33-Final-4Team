// 대출 정보 연결 (선택, 11/18): 카드 연결 다음 화면. [대출 정보 연결하기]를 누른 사람에게만 대출 정보 조회 동의를 보여 준다
// 11/18 피드백: 대출은 앞(계좌 연결)에서 묻고 확인은 저축 확인 뒤에 나와 흐름이 끊겼다 → 동의 · 불러오기 · 확인을 여기서 이어서 한다
//   [동의하고 불러오기] = 동의 저장 → 연결한 은행만 다시 불러오기(POST /api/finance/sync/, 은행 연결이 대출도 같이 조회) → 대출 확인
//   [대출이 없어요] = 동의 없이 연결 결과로. [은행 밖 대출만 직접 등록할게요] = 동의 없이 대출 확인(직접 등록)으로
// 대출 기관은 따로 로그인하지 않는다 (docs/api/finance-connect.md: 대출은 은행 연결로 함께 조회)
// 연결 관리에서 왔으면(state.from === 'manage') 바로 동의 칸을 펼치고, 대출 확인을 마치면 연결 관리로 돌아간다
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Mascot from '../components/Mascot.jsx'
import ChatAsk from '../components/ChatAsk.jsx'
import KushongRunner from '../components/KushongRunner.jsx'
import ScopeConsent, { saveScopeConsent, useScopeConsent } from '../components/ScopeConsent.jsx'
import { apiSend } from '../utils/api.js'
import { runFinanceSync } from '../utils/financeSync.js'
import { PATHS } from '../routes.js'

export default function ConnectLoanPage() {
  const navigate = useNavigate()
  const manage = useLocation().state?.from === 'manage'
  const from = manage ? 'manage' : 'onboarding'
  const [connect, setConnect] = useState(manage)
  const consent = useScopeConsent('loan')
  const [phase, setPhase] = useState('idle')  // idle · saving · syncing
  const [error, setError] = useState('')
  const active = useRef(false)
  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])

  const next = async () => {
    if (phase !== 'idle') return
    if (!consent.docs) return setError('동의 문서를 불러온 뒤 동의해 주세요.')
    if (!consent.allAgreed) return setError('대출 정보를 불러오려면 대출 정보 조회에 동의해 주세요.')
    setError('')
    setPhase('saving')
    try {
      await saveScopeConsent(consent)
      if (!active.current) return
      setPhase('syncing')
      const connections = await apiSend('/api/finance/connections/', 'GET')
      if (!active.current) return
      if (!Array.isArray(connections?.items)) throw new Error('연결된 은행 목록을 불러오지 못했어요. 다시 시도해 주세요.')
      const banks = connections.items.filter((c) => c.businessType === 'bank')
      if (!banks.length) throw new Error('조회할 은행이 없어요. 은행을 연결한 뒤 다시 시도해 주세요.')
      const result = await runFinanceSync({ connectionIds: banks.map((c) => c.connectionId) })
      if (!active.current) return
      for (const bank of banks) {
        const item = result?.items?.find((row) => row.connectionId === bank.connectionId)
        if (item?.status === 'succeeded' && item.parts?.some((p) => p.kind === 'loans' && p.status === 'succeeded')) continue
        throw new Error(item?.error?.message || `${bank.organizationName || '연결한 은행'} 대출 정보를 불러오지 못했어요. 다시 시도해 주세요.`)
      }
      navigate(PATHS.loanCheck, { replace: manage, state: { from } })
    } catch (err) {
      if (!active.current) return
      setPhase('idle')
      if (err.code === 'document_outdated') consent.reload()
      setError(err instanceof TypeError ? '서버에 연결하지 못했어요. 다시 시도해 주세요.' : err.message)
    }
  }

  if (phase === 'syncing') return (
    <Layout title="대출 정보 연결" showBack>
      {!manage && <StepBar step={2} />}
      <div className="mc-making" role="status" aria-live="polite">
        <KushongRunner size={120} />
        <h1>대출 정보를 불러오고 있어요</h1>
        <p className="mc-muted">연결한 은행에서 받은 대출을 찾고 있어요</p>
      </div>
    </Layout>
  )

  const foot = connect
    ? <button type="button" className="mc-primary" disabled={phase !== 'idle'} onClick={next}>{phase === 'saving' ? '동의 저장 중…' : '동의하고 불러오기'}</button>
    : <>
      <button type="button" className="mc-primary" onClick={() => setConnect(true)}>대출 정보 연결하기</button>
      <button type="button" className="mc-o3-ghost" onClick={() => navigate(PATHS.connectDone)}>대출이 없어요</button>
    </>

  return (
    <Layout title="대출 정보 연결" showBack foot={foot}>
      {!manage && <StepBar step={2} />}
      <div className="mc-chat">
        <div className="mc-o2-line">
          <Mascot mood="planner" size={64} />
          <p className="mc-o2-bubble">갚고 있는 <b>대출</b>이 있으면 상환액을 꼭 나갈 돈으로 챙겨 둘게요.</p>
        </div>
        {connect && <>
          <ChatAsk>대출 정보 조회에 동의해 주세요</ChatAsk>
          <ScopeConsent consent={consent} onChange={() => setError('')} note="연결한 은행에서 받은 대출을 불러와요. 대출 기관에 따로 로그인하지 않아요." />
        </>}
        <button type="button" className="mc-link mc-loan-manual" onClick={() => navigate(PATHS.loanCheck, { state: { from } })}>
          학자금 · 캐피탈처럼 은행 밖 대출만 있나요? 직접 등록하기 ›
        </button>
        {error && <div className="mc-error" role="alert">{error}</div>}
      </div>
    </Layout>
  )
}
