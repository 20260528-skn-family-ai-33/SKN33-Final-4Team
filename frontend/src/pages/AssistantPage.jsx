// 통합 챗봇 — 처음부터 자유 입력창 + 기능 버튼 (요구사항 CHT-001·002, backend/chat/router_core/MEMO.md 25절 2단계)
// - 보내기: POST /api/chat/messages/ — 문장 {text} · 기능 버튼 {choice: value} · 답변 선택지 {choice: {value, turn}} (꺼짐 엔진은 문자열 value)
// - 테스트 환경 띠: 서버가 testMode · today(고정 기준일)를 주면 '테스트 환경 · 기준일 …' (켜짐 엔진 + CHAT_TODAY 일 때만)
// - 기능 버튼 목록은 서버가 준다 (첫 화면 = GET /api/chat/messages/, 그 뒤 응답마다 features). 꺼짐(legacy) 2개 · 켜짐(router) 3개
// - 답 모양: 켜짐 = 카드 · 그래프 · 선택지(ChatReply), 꺼짐 = 옛 답 {text, choices} 말풍선. 둘 다 ChatReply 가 그린다
// - 담당 표시 · 예산 폼은 서버가 준 content.agent · content.inputs 로만 정한다 (문장으로 추측하지 않음)
// - 새로고침하면 이 대화를 다시 불러온다 (11/3, GET /api/chat/conversations/<id>/). 대화 번호는 sessionStorage 에 두고
//   채팅 화면을 떠나면 지운다 → 다른 화면에 갔다 오면 지금처럼 빈 대화 (10/30 · 11/2 결정 유지). 404(다른 계정 · 없는 대화)면 새 대화
import { useEffect, useRef, useState } from 'react'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import AdjustForm from '../components/AdjustForm.jsx'
import ChatReply, { Choices } from '../components/ChatReply.jsx'
import { adjustSentence } from '../utils/adjust.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import '../styles/chat.css'

const AGENTS = { spending: '소비 분석', budget: '예산 조정', policy: '청년 정책' }
// 기능 버튼 모양 (목록 · 순서는 서버가 준 features 그대로, 모르는 값은 서버 이름 그대로)
const LOOK = {
  spending: { icon: 'ChartNoAxesCombined', text: '소비를 분석해주세요' },
  budget: { icon: 'SlidersHorizontal', text: '예산을 조정하고 싶어요' },
  policy: { icon: 'Landmark', text: '청년 정책을 추천받고 싶어요' },
}
// 개발 모드 · 켜짐 엔진에서만: 가짜 해석이 아는 문장 (backend/chat/llm/fake.py · legacy.SAID)
const EXAMPLES = ['이번 달 얼마 썼어?', '그럼 교통비는?', '이번 달 3개월 식비', '커피 얼마나 썼어?', '지난달보다 식비 더 썼어?',
  '왜 식비가 늘었어?', '최근 6개월 식비 어때?', '소비 분석해줘', '최근 3개월 식비 예산 얼마나 남았어?', '식비 예산 줄여줘', '날씨 어때?',
  '이번 달 카드 얼마 긁었지?', '과자서원에서 얼마 썼어?', '식비는 왜 늘었을까?', '제일 많이 쓴 데는 왜 많이 나왔을까?',
  '지난달 식비는 왜 많이 나왔어?', '지난주 식비는 왜 많이 나왔어?', '이번 달 주말 식비 왜 늘었어?',
  '커피를 자주 사 먹어서 그래?', '점심값이 비싸진 거야?', '점심을 더 자주 먹어서 그래?', '건당 금액이 올라서야?', '배달 때문이야?']

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

// 새로고침 복원용 대화 번호 — 저장소를 못 쓰는 브라우저(사생활 보호 모드 등)는 그냥 새 대화
const SAVED = 'mc-chat-conversation'
function savedId() {
  try { return sessionStorage.getItem(SAVED) } catch { return null }
}
function saveId(id) {
  try { if (id) sessionStorage.setItem(SAVED, id); else sessionStorage.removeItem(SAVED) } catch { /* 저장 못 해도 대화는 계속 */ }
}

export default function AssistantPage() {
  const first = useApi('/api/chat/messages/')  // 첫 화면의 엔진 · 기능 버튼 (저장 없음)
  const [engine, setEngine] = useState(null)   // 응답마다 서버가 다시 알려 준 값 { engine, features, testMode, today }
  const [restoreId] = useState(savedId)        // 화면을 열 때 남아 있던 번호 = 새로고침 전 대화
  const [conversationId, setConversationId] = useState(restoreId)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(() => Boolean(restoreId))  // 복원 중에는 전송하지 않는다
  const [pending, setPending] = useState(null)  // 실패한 요청은 같은 키로 재시도

  const [error, setError] = useState(null)
  const inFlight = useRef(false)  // React가 다시 그리기 전의 연속 클릭도 한 번만 보낸다
  const end = useRef(null)

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, busy])

  // 번호를 저장해 두고, 화면을 떠나면(언마운트) 지운다. 새로고침은 언마운트 없이 페이지가 바뀌어 번호가 남는다
  useEffect(() => {
    saveId(conversationId)
    return () => saveId(null)
  }, [conversationId])

  // 새로고침 전 대화 다시 불러오기. 404 = 다른 계정 · 없는 대화 → 번호를 지우고 새 대화. 그 밖의 실패는 안내만 (대화를 비우지 않음)
  useEffect(() => {
    if (!restoreId) return
    let live = true
    apiSend(`/api/chat/conversations/${restoreId}/`, 'GET')
      .then((talk) => { if (live) setMessages(talk.messages) })
      .catch((err) => {
        if (!live) return
        if (err.status === 404) setConversationId(null)
        else setError(err.status === 403 ? '로그인해 주세요.' : err.message)
      })
      .finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [restoreId])

  const info = engine ?? first.data
  const blocked = busy || pending !== null
  const send = async (body, said) => {
    if (busy || inFlight.current || (pending && body)) return
    const request = pending ?? {
      body: { conversationId, ...body }, said: said ?? null, key: newRequestKey(),
    }
    inFlight.current = true
    setPending(request)
    setBusy(true)
    setError(null)
    try {
      const out = await apiSend('/api/chat/messages/', 'POST', request.body, { requestKey: request.key })
      if (!out?.conversationId || !Array.isArray(out.messages)) throw new Error('답변을 정상적으로 받지 못했어요.')
      setConversationId(out.conversationId)
      setMessages((m) => [...m, ...out.messages])
      setEngine({ engine: out.engine, features: out.features, testMode: out.testMode, today: out.today })
      setPending(null)
    } catch (err) {
      // 서버가 명시적으로 거절한 요청은 수정할 수 있게 돌려준다. 타임아웃 · 통신/서버 오류는 성공 여부가 불명확하다.
      if (err.status >= 400 && err.status < 500 && err.status !== 408) {
        setPending(null)
        if (request.body.text) setText(request.body.text)
        if (err.fields?.conversationId) setConversationId(null)  // 끝났거나 남의 대화 → 다음 문장부터 새 대화
      }
      setError(err.status === 403 ? '로그인해 주세요.' : err.message)
    } finally {
      inFlight.current = false
      setBusy(false)
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

  return (
    <Layout
      title="통합 챗봇"
      activeTab="assistant"
      foot={
        <form className="mc-chat-in mc-asst-in" onSubmit={submit}>
          <input aria-label="챗봇에게 보낼 메시지" autoComplete="off" placeholder="예: 이번 달 얼마 썼어?" type="text" maxLength={500}
            value={text} disabled={blocked} onChange={(e) => setText(e.target.value)} />
          <button type="submit" className="mc-chat-send" aria-label="보내기" disabled={blocked}><Icon name="Send" /></button>
        </form>
      }
    >
      <div className="mc-bc mc-asst">
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
          {first.error && !info && (
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
                <div className={`mc-msg bot${c.result ? ' mc-asst-card' : ''}`}>
                  <ChatReply content={c} title={AGENTS[c.agent] ?? '머니쿠션'} />
                </div>
                {import.meta.env.DEV && c.devTrace && <Trace trace={c.devTrace} />}
                <Choices choices={c.choices} active={isLast && !blocked} onPick={(p) => pick(c, p)} />
                {(c.inputs ?? []).includes('budget_form') && (
                  <div className="mc-msg bot mc-asst-form">
                    <AdjustForm done={!isLast || blocked} onSubmit={(slots) => send({ text: adjustSentence(slots) }, adjustSentence(slots))} />
                  </div>
                )}
              </div>
            )
          })}
          {pending?.said && <div className="mc-asst-turn"><div className="mc-msg me"><div className="mc-bubble">{pending.said}</div></div></div>}
          {busy && <div className="mc-asst-route is-busy" role="status"><Icon name="Loader" /> 답을 준비하고 있어요…</div>}
          {error && <div className="mc-msg bot"><div className="mc-bubble" role="alert">
            <p>{error}</p>
            {pending && !busy && <>
              <p>답변을 받았는지 확인하지 못했어요. 같은 메시지를 다시 보내 주세요.</p>
              <button type="button" className="mc-btn" onClick={() => send()}>다시 보내기</button>
            </>}
          </div></div>}
          <div ref={end} />
        </div>

        {import.meta.env.DEV && info?.engine === 'router' && (
          <details className="mc-fx-dev">
            <summary>개발용 · 가짜 해석 예시 문장</summary>
            <p>더미 A(윤서)에서 확인: 새 대화 → 지난달보다 식비 더 썼어? → 점심값이 비싸진 거야? / 커피를 자주 사 먹어서 그래?</p>
            <p>점심은 대상의 평균과 가격 판단의 한계를, 커피는 품목 식별 불가를 안내해야 합니다. 기간 확인은 새 대화 → 지난달 식비는 왜 많이 나왔어? → 그럼 교통비는?</p>
            <div className="mc-chipbar">
              <button type="button" disabled={blocked} onClick={startOver}>새 대화</button>
              {EXAMPLES.map((q) => <button key={q} type="button" disabled={blocked} onClick={() => send({ text: q }, q)}>{q}</button>)}
            </div>
          </details>
        )}
      </div>
    </Layout>
  )
}
