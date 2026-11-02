// 통합 챗봇 — 더미 서비스 실행(5174) 전용 화면 (backend/chat/router_core/MEMO.md 22절 2 · 3단계)
// vite.fixture.config.js 가 pages/AssistantPage.jsx 대신 이 파일을 연결한다. 팀 화면(5173)은 그대로
// 틀 · 클래스는 팀 AssistantPage 와 같게 (추천 질문 버튼 · 말풍선 · 연결 안내 · 입력창 · 예산 고르기 AdjustForm)
// - 보내기: POST /api/chat/messages/ (8001 의 새 라우터 + 소비 분석 에이전트, 가짜 해석 = 정해 둔 문장만)
//   문장 {text} · 기능 버튼 {choice: 'spending' | 'budget' | 'policy'} · 답변 선택지 {choice: {value, turn}}
// - 담당 표시 · 예산 폼은 서버가 준 content.agent · content.inputs 로만 정한다 (문장으로 추측하지 않음)
// - 채팅방을 나갔다 오거나 새로고침하면 빈 대화로 시작한다 (사용자 결정 — 지난 대화를 다시 불러오지 않음. 대화 번호는 화면 안에서만)
import { useEffect, useRef, useState } from 'react'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import AdjustForm from '../components/AdjustForm.jsx'
import { adjustSentence } from '../utils/adjust.js'
import { apiSend, newRequestKey } from '../utils/api.js'
import ChatReply, { Choices } from './ChatReply.jsx'
import './fixture.css'

const AGENTS = { spending: '소비 분석', budget: '예산 조정', policy: '청년 정책' }
// 기능 버튼 3개 (팀 화면의 추천 질문 모양. 큰 지출 상담 → 청년 정책, 22-12 결정)
const FEATURES = [
  { choice: 'spending', icon: 'ChartNoAxesCombined', text: '소비를 분석해주세요' },
  { choice: 'budget', icon: 'SlidersHorizontal', text: '예산을 조정하고 싶어요' },
  { choice: 'policy', icon: 'Landmark', text: '청년 정책을 추천받고 싶어요' },
]
// 개발용: 가짜 해석이 아는 문장 (backend/spending_fixture/fake_reading.py · legacy.SAID)
const EXAMPLES = ['이번 달 얼마 썼어?', '그럼 교통비는?', '이번 달 3개월 식비', '커피 얼마나 썼어?', '지난달보다 식비 더 썼어?',
  '왜 식비가 늘었어?', '최근 6개월 식비 어때?', '소비 분석해줘', '최근 3개월 식비 예산 얼마나 남았어?', '식비 예산 줄여줘', '날씨 어때?',
  '이번 달 카드 얼마 긁었지?', '과자서원에서 얼마 썼어?', '식비는 왜 늘었을까?', '제일 많이 쓴 데는 왜 많이 나왔을까?']

export default function FixtureAssistantPage() {
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(null)  // 답을 기다리는 동안 먼저 보여 줄 내 말 (서버 답이 오면 서버 메시지로 바뀜)
  const [error, setError] = useState(null)
  const end = useRef(null)

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, busy])

  const send = async (body, said) => {
    if (busy) return
    setBusy(true)
    setSent(said ?? null)
    setError(null)
    try {
      const out = await apiSend('/api/chat/messages/', 'POST', { conversationId, ...body }, { requestKey: newRequestKey() })
      setConversationId(out.conversationId)
      setMessages((m) => [...m, ...out.messages])
    } catch (err) {
      if (err.fields?.conversationId) setConversationId(null)  // 끝났거나 남의 대화 → 다음 문장부터 새 대화
      setError(err.status === 403 ? '로그인해 주세요.' : err.message)
    }
    setSent(null)
    setBusy(false)
  }

  const submit = (e) => {
    e.preventDefault()
    const q = text.trim()
    if (!q) return
    setText('')
    send({ text: q }, q)
  }

  const startOver = () => {
    setConversationId(null)
    setMessages([])
    setError(null)
  }

  const bots = messages.filter((m) => m.role === 'bot')
  const last = bots.at(-1)
  const agent = last?.content.agent ?? null

  const features = (
    <div className="mc-asst-opts" role="group" aria-label="기능 고르기">
      {FEATURES.map((f) => (
        <button key={f.choice} type="button" className="mc-asst-opt" disabled={busy} onClick={() => send({ choice: f.choice }, f.text)}>
          <span className="mc-asst-ic"><Icon name={f.icon} /></span>
          <span className="mc-grow">{f.text}</span>
          <Icon name="ChevronRight" />
        </button>
      ))}
    </div>
  )

  // 담당이 바뀐 답변 앞에만 '… 도우미에 연결했어요' (팀 화면과 같은 안내)
  const switched = new Set()
  messages.reduce((prev, m) => {
    if (m.role !== 'bot' || !m.content.agent) return prev
    if (m.content.agent !== prev) switched.add(m.messageId)
    return m.content.agent
  }, null)

  return (
    <Layout
      title="통합 챗봇"
      activeTab="assistant"
      foot={
        <form className="mc-chat-in mc-asst-in" onSubmit={submit}>
          <input aria-label="챗봇에게 보낼 메시지" autoComplete="off" placeholder="예: 이번 달 얼마 썼어?" type="text" maxLength={500}
            value={text} onChange={(e) => setText(e.target.value)} />
          <button type="submit" className="mc-chat-send" aria-label="보내기" disabled={busy}><Icon name="Send" /></button>
        </form>
      }
    >
      <div className="mc-bc mc-asst">
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
                <Choices choices={c.choices} active={isLast && !busy} onPick={(p) => send({ choice: { value: p.value, turn: c.turn } }, p.label)} />
                {(c.inputs ?? []).includes('budget_form') && (
                  <div className="mc-msg bot mc-asst-form">
                    <AdjustForm done={!isLast} onSubmit={(slots) => send({ text: adjustSentence(slots) }, adjustSentence(slots))} />
                  </div>
                )}
              </div>
            )
          })}
          {busy && sent && <div className="mc-asst-turn"><div className="mc-msg me"><div className="mc-bubble">{sent}</div></div></div>}
          {busy && <div className="mc-asst-route is-busy" role="status"><Icon name="Loader" /> 답변을 준비하고 있어요…</div>}
          {error && <div className="mc-msg bot"><div className="mc-bubble" role="alert">{error}</div></div>}
          <div ref={end} />
        </div>

        <details className="mc-fx-dev">
          <summary>개발용 · 가짜 해석 예시 문장</summary>
          <div className="mc-chipbar">
            <button type="button" onClick={startOver}>새 대화</button>
            {EXAMPLES.map((q) => <button key={q} type="button" disabled={busy} onClick={() => send({ text: q }, q)}>{q}</button>)}
          </div>
        </details>
      </div>
    </Layout>
  )
}
