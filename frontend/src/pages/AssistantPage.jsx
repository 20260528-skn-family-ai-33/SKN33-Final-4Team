// 통합 챗봇 — 처음부터 자유 입력창 + 기능 버튼 (요구사항 CHT-001·002, backend/chat/router_core/MEMO.md 25절 2단계)
// - 보내기: POST /api/chat/messages/ — 문장 {text} · 기능 버튼 {choice: value} · 답변 선택지 {choice: {value, turn}} (꺼짐 엔진은 문자열 value)
// - 테스트 환경 띠: 서버가 testMode · today(고정 기준일)를 주면 '테스트 환경 · 기준일 …' (켜짐 엔진 + CHAT_TODAY 일 때만)
// - 기능 버튼 목록은 서버가 준다 (첫 화면 = GET /api/chat/messages/, 그 뒤 응답마다 features). 꺼짐(legacy) 2개 · 켜짐(router) 3개
// - 답 모양: 켜짐 = 카드 · 그래프 · 선택지(ChatReply), 꺼짐 = 옛 답 {text, choices} 말풍선. 둘 다 ChatReply 가 그린다
// - 담당 표시 · 예산 폼은 서버가 준 content.agent · content.inputs 로만 정한다 (문장으로 추측하지 않음)
// - 새로고침하면 이 대화를 다시 불러온다 (11/3, GET /api/chat/conversations/<id>/). 대화 번호는 sessionStorage 에 두고
//   다른 화면을 다녀와도 유지한다. 새 대화·로그아웃·404(다른 계정·없는 대화)에서 초기화한다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import AdjustForm from '../components/AdjustForm.jsx'
import ChatReply, { Choices, Resync } from '../components/ChatReply.jsx'
import { policyChoices } from '../utils/policyCards.js'
import BudgetApprovalPopup from '../popups/BudgetApprovalPopup.jsx'
import BudgetAdjustConsentPopup from '../popups/BudgetAdjustConsentPopup.jsx'
import CardCredentialPopup from '../popups/CardCredentialPopup.jsx'
import { usePopup } from '../popups/context.js'
import { PATHS } from '../routes.js'
import { adjustmentChoices, adjustSentence } from '../utils/adjust.js'
import { catName } from '../utils/categories.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { needsCardValues, runFinanceSync, syncLines } from '../utils/financeSync.js'
import { savedId, saveId } from '../utils/chatConversation.js'
import { approvalPath, forgetPendingApproval, readPendingApproval, rememberPendingApproval } from '../utils/budgetApproval.js'
import '../styles/chat.css'

const AGENTS = { spending: '소비 분석', budget: '예산 조정', policy: '청년 정책' }
// 기능 버튼 모양 (목록 · 순서는 서버가 준 features 그대로, 모르는 값은 서버 이름 그대로)
const LOOK = {
  spending: { icon: 'ChartNoAxesCombined', text: '소비를 분석해주세요' },
  budget: { icon: 'SlidersHorizontal', text: '예산을 조정하고 싶어요' },
  policy: { icon: 'Landmark', text: '청년 정책을 추천받고 싶어요' },
}
// 개발 모드 전용: B 증감 설명의 분석 과정 (서버가 더미 설정에서만 devTrace 를 준다 — 서비스 응답 칸 아님, MEMO 26)
const STOP = { insufficient: '근거 부족 — 서버가 멈춤', nothing_more: '더 볼 묶음 없음 · 상한 — 서버가 멈춤' }
const FALLBACK = { plan_failed: '계획 호출 실패 → 서버 설명', invalid_plan: '계획 형식 오류 → 서버 설명', template: '근거 부족이라 지금 설명(거절 · 안내)', rejected: '검사 탈락 → 지금 템플릿 설명', timeout: '시간 초과 → 지금 템플릿 설명',
  answer_failed: '답 LLM 실패 → 지금 템플릿 설명', no_time: '답 기다릴 시간 부족 → 지금 템플릿 설명' }

function Trace({ trace }) {
  const query = (q) => q.by + (q.focus ? `(${q.focus})` : '') + (q.metric ? ` · ${q.metric}` : '')
  const reasons = { already_supported: '기존 근거로 충분', unsupported_target: '자료에서 구분할 수 없는 대상', outside_scope: '조회 범위 밖',
    missing_target_metric: '질문한 대상·지표를 비교', unrequested_target: '질문에 없는 대상', different_metric: '증가액과 단위가 달라 생략', empty_average: '한쪽 기간에 결제 없음',
    missing_time_or_merchant: '시간·가게 정보 부족' }
  const entry = trace.entry
  const planned = 'stop' in trace  // B 흐름까지 갔는지 (아니면 B 전 단계만)
  return (
    <details className="mc-fx-dev">
      <summary>분석 과정 (개발용)</summary>
      <ul className="mc-spend-lines">
        {entry && <li>해석: {JSON.stringify(entry.reading)}</li>}
        {entry && <li>dialog: {entry.decision.status}{entry.decision.reason ? ` · ${entry.decision.reason}` : ''}{entry.decision.asking ? ` · 묻기 ${entry.decision.asking}` : ''}{entry.items.length > 0 && ` · 조회 ${entry.items.map((i) => `${i.type}${i.category ? `/${i.category}` : ''}`).join(', ')}`}</li>}
        {entry && <li>설명 요청: {entry.explain ? '예 — B 로' : '아니요'}</li>}
        {entry && <li>추가 판단: {trace.agent ? `${trace.agent.status}${trace.agent.fallback ? ` · 멈춘 이유 ${trace.agent.fallback}` : ''}` : '안 함'}</li>}
        {trace.agent && <li>선택: {trace.agent.choice ? `${trace.agent.choice.tool ?? 'answer'}${trace.agent.choice.args?.by ? `(${trace.agent.choice.args.by})` : ''}${trace.agent.toolStatus ? ` · ${trace.agent.toolStatus}` : ''}` : '판단 호출 없음'}{trace.agent.unmet.length ? ` · 못 확인 ${trace.agent.unmet.map((n) => n?.by ?? n?.tool ?? '나머지').join(', ')}` : ''}</li>}
        {planned && <li>멈춤: {trace.stop ? STOP[trace.stop] : '없음 — 계획 LLM 이 정함'}</li>}
        {trace.plan && <li>계획: {trace.plan.queries.length ? trace.plan.queries.map(query).join(', ') : '추가 조회 없음'} · {trace.plan.reason}</li>}
        {planned && trace.plan === null && <li>계획: 응답 없음 · 서버 설명으로 대체</li>}
        {(trace.dropped ?? []).length > 0 && <li>버린 조회(서버 검사): {trace.dropped.map(query).join(', ')}</li>}
        {trace.plan && <li>가설: {trace.hypothesis ? `${trace.hypothesis.target ?? trace.hypothesis.axis} · ${trace.hypothesis.metric ?? trace.hypothesis.axis}${trace.hypothesis.subject ? `(${trace.hypothesis.subject})` : ''}` : '없음'}{(trace.must ?? []).length > 0 && ` · 꼭 쓸 주장 ${trace.must.map((g) => g.join('/')).join(', ')}`}</li>}
        {trace.support && <li>근거: {trace.support.status === 'observation' ? '질문 대상의 비교 근거 확인' : '확인 불가'}{trace.support.limitation && ` · ${trace.support.limitation}`}</li>}
        {(trace.queryDecisions ?? []).map((d, n) => <li key={n}>{d.action === 'executed' ? '실행' : '생략'}: {query(d.query)} · {reasons[d.reason] ?? d.reason}</li>)}
        {(trace.unusedQueries ?? []).length > 0 && <li>답에 안 쓴 조회: {trace.unusedQueries.join(', ')}</li>}
        {(trace.used ?? []).length > 0 && <li>쓴 주장: {trace.used.join(', ')}</li>}
        {trace.check && <li>검사: {trace.check.length ? `탈락 ${trace.check.join(', ')}` : '통과'}</li>}
        {planned && <li>결과: {trace.fallback ? FALLBACK[trace.fallback] ?? trace.fallback : trace.support?.status === 'insufficient' ? '확인 불가 안내' : 'B 답'}</li>}
      </ul>
    </details>
  )
}

const wideAdjustment = (a) => Boolean(a && (a.proposal || a.comparison || a.alternatives))

export default function AssistantPage() {
  const { state: navState } = useLocation()
  const navigate = useNavigate()
  const { openPopup, popup } = usePopup()
  const [storedApproval] = useState(readPendingApproval)
  const recovery = navState?.designRecovery
  const [budgetEntry] = useState(() => storedApproval ? null : navState?.budgetContext ?? null)
  const [entryWaiting, setEntryWaiting] = useState(Boolean(budgetEntry))
  const entryStarted = useRef(false)
  const first = useApi('/api/chat/messages/')  // 첫 화면의 엔진 · 기능 버튼 (저장 없음)
  const [engine, setEngine] = useState(null)   // 응답마다 서버가 다시 알려 준 값 { engine, features, testMode, today }
  const [restoreId] = useState(() => storedApproval?.conversationId ?? recovery?.conversationId ?? (budgetEntry ? null : savedId()))
  const [restoreVersion, setRestoreVersion] = useState(0)
  const [conversationId, setConversationId] = useState(restoreId)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(() => Boolean(restoreId))  // 복원 중에는 전송하지 않는다
  const [pending, setPending] = useState(null)  // 실패한 요청은 같은 키로 재시도
  const [approvalPending, setApprovalPending] = useState(null)
  const [approvalRecovery, setApprovalRecovery] = useState(Boolean(storedApproval))

  const [error, setError] = useState(null)
  const [recoveryExpired, setRecoveryExpired] = useState(false)
  const [resync, setResync] = useState(null)  // [내역 다시 불러오기] { id: 봇 메시지, failed }
  const cardWait = useRef(null)  // 카드 값 입력 팝업을 기다리는 중 (닫히면 false 로 끝)
  const inFlight = useRef(false)  // React가 다시 그리기 전의 연속 클릭도 한 번만 보낸다
  const mounted = useRef(false)
  const latest = useRef({})  // 가장 최근에 그린 send · openPopup (동의 창이 닫힌 뒤 거절됐던 요청을 다시 보낼 때 쓴다)
  const end = useRef(null)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, busy])

  // 메뉴·다른 탭을 다녀와도 같은 대화를 복원한다. 본문은 저장하지 않고 서버에서 다시 읽는다.
  useEffect(() => {
    saveId(conversationId)
  }, [conversationId])

  useEffect(() => {
    if (storedApproval && navState?.budgetContext) navigate(PATHS.assistant, { replace: true, state: null })
  }, [storedApproval, navState, navigate])

  // 저장된 대화 다시 불러오기. 404 = 다른 계정 · 없는 대화 → 번호를 지우고 새 대화. 그 밖의 실패는 안내만 (대화를 비우지 않음)
  useEffect(() => {
    if (!restoreId) return
    let live = true
    apiSend(`/api/chat/conversations/${encodeURIComponent(restoreId)}/`, 'GET')
      .then((talk) => {
        if (!live) return
        if (!Array.isArray(talk.messages) || talk.conversationId !== restoreId) throw new Error('대화를 정상적으로 받지 못했어요.')
        setMessages(talk.messages)
        if (!storedApproval) return
        const proposal = talk.messages.map((m) => m.content.budgetAdjustment?.proposal)
          .filter((item) => item?.proposalId === storedApproval.proposalId).at(-1)
        if (!proposal || storedApproval.path !== approvalPath(proposal)) {
          forgetPendingApproval(storedApproval.conversationId, storedApproval.proposalId)
          setApprovalRecovery(false)
        } else if (proposal.state === 'awaiting_approval') {
          setApprovalPending({ ...storedApproval, proposal })
          setApprovalRecovery(false)
        } else if (['saved', 'expired', 'canceled', 'rejected', 'failed'].includes(proposal.state)) {
          forgetPendingApproval(storedApproval.conversationId, storedApproval.proposalId)
          setApprovalRecovery(false)
        } else {
          setError('확정 상태를 아직 확인하지 못했어요. 다시 불러와 주세요.')
        }
      })
      .catch((err) => {
        if (!live) return
        if (err.status === 404) {
          setConversationId(null)
          if (recovery?.conversationId) { setRecoveryExpired(true); setError('예산 조정 대화를 찾지 못했어요. 다시 추천받아 주세요.') }
          if (storedApproval) forgetPendingApproval(storedApproval.conversationId, storedApproval.proposalId)
          setApprovalRecovery(false)
        }
        else setError(err.status === 403 ? '로그인해 주세요.' : err.message)
      })
      .finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [restoreId, restoreVersion, storedApproval, recovery?.conversationId])

  const info = engine ?? first.data
  const entryUnavailable = Boolean(budgetEntry && (info ? info.engine !== 'router' : first.error))
  const blocked = busy || pending !== null || Boolean(resync && !resync.failed) || approvalPending !== null || approvalRecovery
    || (entryWaiting && !entryUnavailable) || Boolean(recovery && !conversationId)
  const returnToRecommendation = useCallback(({ canceled = false, expired = false } = {}) => {
    navigate(PATHS.recommend, { replace: true, state: { recoveryReturn: { sourceTaskId: recovery?.sourceTaskId, canceled, expired } } })
  }, [navigate, recovery?.sourceTaskId])
  const send = useCallback(async (body, said) => {
    if (busy || inFlight.current || approvalPending || approvalRecovery || (pending && body)) return
    const request = pending ?? {
      body: { conversationId, ...body }, said: said ?? null,
      key: body?.budgetContext?.mode === 'DESIGN_RECOVERY' && recovery?.requestKey ? recovery.requestKey : newRequestKey(),
    }
    inFlight.current = true
    setEntryWaiting(false)
    setPending(request)
    setBusy(true)
    setError(null)
    try {
      const out = await apiSend('/api/chat/messages/', 'POST', request.body, { requestKey: request.key })
      if (!out?.conversationId || !Array.isArray(out.messages)) throw new Error('답변을 정상적으로 받지 못했어요.')
      if (!mounted.current) return
      setConversationId(out.conversationId)
      setMessages((m) => [...m, ...out.messages])
      setEngine({ engine: out.engine, features: out.features, testMode: out.testMode, today: out.today })
      setPending(null)
      const adjustment = out.messages.at(-1)?.content?.budgetAdjustment
      if (recovery && adjustment?.sourceTaskId === recovery.sourceTaskId && adjustment.status === 'ended') {
        returnToRecommendation({ canceled: true })
      } else if (recovery && request.body.budgetContext?.mode === 'DESIGN_RECOVERY') {
        navigate(PATHS.assistant, { replace: true, state: { designRecovery: {
          sourceTaskId: recovery.sourceTaskId, conversationId: out.conversationId,
        } } })
      }
    } catch (err) {
      // 서버가 명시적으로 거절한 요청은 수정할 수 있게 돌려준다. 타임아웃 · 통신/서버 오류는 성공 여부가 불명확하다.
      if (err.status >= 400 && err.status < 500 && err.status !== 408) {
        setPending(null)
        if (request.body.text) setText(request.body.text)
        if (err.fields?.conversationId) setConversationId(null)  // 끝났거나 남의 대화 → 다음 문장부터 새 대화
      }
      const expired = recovery && (err.code === 'expired' || err.status === 404)
      if (expired) setRecoveryExpired(true)
      // 예산 조정 AI 전송 동의 이력이 없다 → 동의 창. 동의하면 거절됐던 요청을 그대로 다시 보낸다 (11/11).
      // 창을 닫으면 아래 안내만 남고 아무것도 보내지 않는다 (입력한 문장은 입력 칸에 그대로 있다)
      const needsConsent = !expired && err.code === 'budget_adjust_consent_required'
      if (needsConsent) {
        latest.current.openPopup(<BudgetAdjustConsentPopup onAgreed={() => {
          setError(null)
          if (request.body.text) setText('')
          latest.current.send(request.body, request.said)
        }} />)
      }
      setError(expired ? '예산 조건이 바뀌었거나 조정 대화가 만료됐어요. 추천 화면에서 다시 추천받아 주세요.'
        : needsConsent ? '예산 조정을 이용하려면 AI 전송 동의가 필요해요. 예산 조정을 다시 요청하면 동의 창이 열려요.' : err.status === 403 ? '로그인해 주세요.' : err.message)
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }, [busy, pending, conversationId, approvalPending, approvalRecovery, recovery, navigate, returnToRecommendation])

  useEffect(() => { latest.current = { send, openPopup } })

  // 일부 내역 답의 [내역 다시 불러오기]: 홈 새로고침과 같은 POST /api/finance/sync/ → 카드 값이 필요한 카드사는 입력 팝업 →
  // 한 곳이라도 불러왔으면 그 답 앞의 사용자 질문을 그대로 다시 보낸다. 아무 데도 못 불러왔으면 그 자리에 실패 + 다시 시도
  useEffect(() => {
    if (popup || !cardWait.current) return
    const done = cardWait.current
    cardWait.current = null
    done(false)  // 입력 팝업을 그냥 닫음 → 그 카드사는 이번에 안 불러온 채로
  }, [popup])
  const askCard = (item) => new Promise((resolve) => {
    cardWait.current = resolve
    openPopup(<CardCredentialPopup item={item} onDone={() => { cardWait.current = null; resolve(true) }} />)
  })
  const reloadAndAsk = async (m) => {
    if (blocked || inFlight.current) return
    const asked = messages.slice(0, messages.indexOf(m)).findLast((x) => x.role === 'user')?.content.text
    setResync({ id: m.messageId, failed: false })
    try {
      const items = (await runFinanceSync()).items ?? []
      let loaded = syncLines({ items: items.filter((it) => !needsCardValues(it)) }).some((l) => l.ok)
      for (const item of items.filter(needsCardValues)) loaded = (await askCard(item)) || loaded
      if (!loaded) throw new Error('불러온 기관 없음')
      if (!mounted.current) return
      setResync(null)
      if (asked) latest.current.send({ text: asked }, asked)
    } catch {
      if (mounted.current) setResync({ id: m.messageId, failed: true })
    }
  }

  const submit = (e) => {
    e.preventDefault()
    const q = text.trim()
    if (!q || blocked || inFlight.current) return
    setText('')
    send({ text: q }, q)
  }

  const startOver = () => {
    if (blocked || inFlight.current) return
    setConversationId(null)
    setMessages([])
    setError(null)
  }

  const bots = messages.filter((m) => m.role === 'bot')
  const last = bots.at(-1)
  const agent = last?.content.agent ?? null

  const features = (
    <div className="mc-asst-opts" role="group" aria-label="기능 고르기">
      {(info?.features ?? []).map((f) => {
        const look = LOOK[f.value] ?? { icon: 'ChevronRight', text: f.label }
        return (
          <button key={f.value} type="button" className="mc-asst-opt" disabled={blocked} onClick={() => send({ choice: f.value }, look.text)}>
            <span className="mc-asst-ic"><Icon name={look.icon} /></span>
            <span className="mc-grow">{look.text}</span>
            <Icon name="ChevronRight" />
          </button>
        )
      })}
    </div>
  )

  // 담당이 바뀐 답변 앞에만 '… 도우미에 연결했어요'
  const switched = new Set()
  messages.reduce((prev, m) => {
    if (m.role !== 'bot' || !m.content.agent) return prev
    if (m.content.agent !== prev) switched.add(m.messageId)
    return m.content.agent
  }, null)

  // 답변 선택지: 켜짐 엔진은 어느 질문의 선택지인지(turn)도 보낸다. 꺼짐(옛 답)은 turn 이 없어 value 문자열만
  const pick = (c, p) => send({ choice: c.turn == null ? p.value : { value: p.value, turn: c.turn } }, p.label)

  const updateProposal = (proposal, status) => setMessages((items) => items.map((message) => {
    const adjustment = message.content.budgetAdjustment
    if (adjustment?.proposal?.proposalId !== proposal.proposalId) return message
    return { ...message, content: { ...message.content, choices: [], budgetAdjustment: {
      ...adjustment, proposal, status, canApprove: false,
    } } }
  }))

  const openApproval = (proposal, request = null) => {
    if (!request && (blocked || inFlight.current)) return
    openPopup(<BudgetApprovalPopup proposal={proposal} request={request}
      onPending={(request) => { rememberPendingApproval(conversationId, request); setApprovalPending(request) }}
      onApproved={(saved) => {
        forgetPendingApproval(conversationId, saved.proposalId)
        setApprovalPending(null)
        updateProposal(saved, 'saved')
        setError(null)
      }}
      onRejected={(chosen, err, ended) => {
        forgetPendingApproval(conversationId, chosen.proposalId)
        setApprovalPending(null)
        if (ended) updateProposal({ ...chosen, canApprove: false, ...(err.code === 'expired' ? { state: 'expired' } : {}) }, err.code === 'expired' ? 'expired' : 'needs_confirmation')
        setError(err.message)
      }} />)
  }

  useEffect(() => {
    if (!budgetEntry || entryStarted.current) return
    if (!info && !first.error) return
    entryStarted.current = true
    // 예산 화면의 초과 카테고리에서 왔으면(budgetContext.hints) 보내는 중 말풍선에 그 카테고리 이름을 넣는다
    const overCode = budgetEntry.hints?.reason === 'over' ? budgetEntry.hints.categories?.[0] : null
    // oxlint-disable-next-line react/set-state-in-effect -- 화면 진입 시 서버 대화를 한 번 시작하며 전송 함수가 진행 상태를 함께 관리한다.
    if (info?.engine === 'router') send({ conversationId: null, choice: 'budget', budgetContext: budgetEntry },
      overCode ? `${catName(overCode)} 예산을 조정하고 싶어요` : '예산을 조정하고 싶어요')
    if (!recovery) navigate(PATHS.assistant, { replace: true, state: null })
  }, [budgetEntry, first.error, info, navigate, recovery, send])

  const recoveryStatus = last?.content.budgetAdjustment
  const recoverySaved = recoveryStatus?.proposal?.sourceTaskId === recovery?.sourceTaskId && recoveryStatus?.proposal?.state === 'saved'

  return (
    <Layout
      title="통합 챗봇"
      activeTab="assistant"
      foot={<>
        {/* 새 대화 (11/11): 대화가 있을 때만 입력란 왼쪽 위에 — 입력란과 함께 화면 아래에 붙어 있어 대화가 길어도 바로 누를 수 있다.
            지금 대화를 화면에서 비우고 다음 문장부터 새 대화로 보낸다 */}
        {!recovery && messages.length > 0 && (
          <button type="button" className="mc-asst-new" disabled={blocked} onClick={startOver}><Icon name="RefreshCw" />새 대화</button>
        )}
        <form className="mc-chat-in mc-asst-in" onSubmit={submit}>
          <input aria-label="챗봇에게 보낼 메시지" autoComplete="off" placeholder="예: 이번 달 얼마 썼어?" type="text" maxLength={500}
            value={text} disabled={blocked} onChange={(e) => setText(e.target.value)} />
          <button type="submit" className="mc-chat-send" aria-label="보내기" disabled={blocked}><Icon name="Send" /></button>
        </form>
      </>}
    >
      <div className="mc-bc mc-asst">
        {recovery && <div className="mc-chipbar" aria-label="분배 실패 조정">
          {recoverySaved
            ? <button type="button" className="mc-btn" onClick={() => navigate(PATHS.budget, { replace: true })}>반영된 예산 보기</button>
            : <>
              {recoveryStatus?.phase === 'design_recovery' && !recoveryExpired && recoveryStatus?.proposal?.state !== 'expired'
                && <button type="button" className="mc-btn" disabled={blocked} onClick={() => send({ text: '취소' }, '취소')}>조정 취소하고 돌아가기</button>}
              <button type="button" className="mc-link" disabled={busy || approvalPending !== null || approvalRecovery}
                onClick={() => returnToRecommendation({ expired: recoveryExpired || recoveryStatus?.proposal?.state === 'expired' })}>추천 화면으로 돌아가기</button>
            </>}
        </div>}
        {info?.testMode && <p className="mc-fx-band" role="note">테스트 환경 · 기준일 {info.today}</p>}
        <div className="mc-bc-ctx mc-asst-ctx">
          <span className="mc-cat-ic mc-big-ic"><Icon name="Sparkles" /></span>
          <div>
            <em className="mc-asst-who">{agent ? `${AGENTS[agent]} 도우미` : '라우터'}</em>
            <strong>머니쿠션 챗봇</strong>
            <small>{agent ? `지금은 ${AGENTS[agent]} 도우미와 대화 중이에요` : '편하게 물어보면 맞는 도우미를 연결해 드려요'}</small>
          </div>
        </div>
        <div className="mc-bc-log mc-chat-log mc-asst-log" aria-live="polite">
          <div className="mc-msg bot">
            <div className="mc-bubble">안녕하세요! 무엇을 도와드릴까요?<br /><small>아래에서 고르거나 하고 싶은 말을 바로 적어 주세요.</small></div>
          </div>
          {features}
          {entryUnavailable && !messages.length && <div className="mc-msg bot"><div className="mc-bubble" role="alert">예산 조정 서비스를 아직 사용할 수 없어요. 잠시 후 다시 열어 주세요.</div></div>}
          {first.error && !info && !entryUnavailable && (
            <div className="mc-msg bot"><div className="mc-bubble" role="alert">{first.error.status === 403 ? '로그인해 주세요.' : first.error.message}</div></div>
          )}

          {messages.map((m) => {
            if (m.role === 'user') {
              return <div key={m.messageId} className="mc-asst-turn"><div className="mc-msg me"><div className="mc-bubble">{m.content.text}</div></div></div>
            }
            const c = m.content
            const isLast = m === last
            return (
              <div key={m.messageId} className="mc-asst-turn">
                {switched.has(m.messageId) && <div className="mc-asst-route"><Icon name="Link" /> {AGENTS[c.agent]} 도우미에 연결했어요</div>}
                {/* 표가 있는 답(소비 분석 카드 · 예산 변경안 · 비교안)만 화면 폭을 다 쓴다. 글만 있는 예산 조정 답은 보통 말풍선 폭 */}
                <div className={`mc-msg bot${c.result || wideAdjustment(c.budgetAdjustment) ? ' mc-asst-card' : ''}`}>
                  <ChatReply content={c} title={AGENTS[c.agent] ?? '머니쿠션'} active={isLast && !blocked}
                    navigation={{ choices: last?.content.budgetAdjustment ? last.content.choices : [], active: !blocked,
                      onPick: (p) => pick(last.content, p) }}
                    onPick={(p) => pick(c, p)} onApprove={(proposal) => openApproval(proposal)}
                    onSend={(text) => { if (isLast && !blocked) send({ text }, text) }} />
                </div>
                {import.meta.env.DEV && c.devTrace && <Trace trace={c.devTrace} />}
                {isLast && c.syncedUntil?.length > 0 && (
                  <Resync state={resync?.id === m.messageId ? resync : null} disabled={blocked} onRun={() => reloadAndAsk(m)} />
                )}
                <Choices choices={policyChoices(c, adjustmentChoices(c))} active={isLast && !blocked} onPick={(p) => pick(c, p)} />
                {!c.budgetAdjustment && (c.inputs ?? []).includes('budget_form') && (
                  <div className="mc-msg bot mc-asst-form">
                    <AdjustForm done={!isLast || blocked} onSubmit={(slots) => send({ text: adjustSentence(slots) }, adjustSentence(slots))} />
                  </div>
                )}
              </div>
            )
          })}
          {pending?.said && <div className="mc-asst-turn"><div className="mc-msg me"><div className="mc-bubble">{pending.said}</div></div></div>}
          {busy && <div className="mc-asst-route is-busy" role="status"><Icon name="Loader" /> 답을 준비하고 있어요…</div>}
          {approvalRecovery && !busy && <div className="mc-msg bot"><div className="mc-bubble" role="alert">
            <p>이전 예산 확정 상태를 먼저 확인해 주세요.</p>
            <button type="button" className="mc-btn" onClick={() => { setBusy(true); setError(null); setRestoreVersion((v) => v + 1) }}>확정 상태 다시 불러오기</button>
          </div></div>}
          {approvalPending && !popup && <div className="mc-msg bot"><div className="mc-bubble" role="alert">
            <p>예산 확정 결과를 아직 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요.</p>
            <button type="button" className="mc-btn" onClick={() => openApproval(approvalPending.proposal, approvalPending)}>확정 결과 다시 확인</button>
          </div></div>}
          {error && <div className="mc-msg bot"><div className="mc-bubble" role="alert">
            <p>{error}</p>
            {pending && !busy && <>
              <p>답변을 받았는지 확인하지 못했어요. 같은 메시지를 다시 보내 주세요.</p>
              <button type="button" className="mc-btn" onClick={() => send()}>다시 보내기</button>
            </>}
            {recovery?.conversationId && !pending && !busy && !recoveryExpired && !approvalRecovery && !messages.length &&
              <button type="button" className="mc-btn" onClick={() => { setBusy(true); setError(null); setRestoreVersion((v) => v + 1) }}>조정 대화 다시 불러오기</button>}
          </div></div>}
          <div ref={end} />
        </div>
      </div>
    </Layout>
  )
}
