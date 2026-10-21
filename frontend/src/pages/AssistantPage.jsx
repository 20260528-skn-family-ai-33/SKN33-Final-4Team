// 통합 챗봇 — 처음부터 자유 입력창 + 추천 질문 (LLM 라우터 방향, 요구사항 CHT-001·002)
// 보낸 문장은 라우터가 의도를 보고 맞는 도우미에 연결한다
// 지금은 백엔드 라우터 전이라 utils/mockRouter.js 로 흐름만 보여 준다 (실제 답변 X)
// 예산 조정으로 연결되면: ① 질문 말풍선 + ② 고르기 말풍선(AdjustForm). 문장에 재료가 있으면 ②를 미리 채운다
// 예산 조정 중에는 "식비 5만원 늘려줘"처럼 적어도 되고, ②를 골라 "조정안 받기"를 눌러도 된다
import { useEffect, useRef, useState } from 'react'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import AdjustForm from '../components/AdjustForm.jsx'
import { adjustSentence, josa, manwon } from '../utils/adjust.js'
import { mockRoute, mockSlots } from '../utils/mockRouter.js'

// 추천 질문 (누르면 그 문장을 보낸 것과 같다)
const SUGGESTIONS = [
  { icon: 'ChartNoAxesCombined', text: '소비를 분석해주세요' },
  { icon: 'SlidersHorizontal', text: '예산을 조정하고 싶어요' },
  { icon: 'Landmark', text: '큰 지출을 상담하고 싶어요', soon: true },
]

// 라우터가 고른 도우미별 안내
const ROUTED = {
  spending: { agent: '소비 분석', reply: '내 소비를 기간별·카테고리별로 정리해 드릴게요.' },
  budget: { agent: '예산 조정', reply: '어느 카테고리를 얼마나 조정할까요? 바로 적어 주시거나 아래에서 골라 주세요.', form: true },
  big: { agent: '큰 지출 상담', reply: '큰 지출 상담은 아직 준비 중이에요. 곧 열릴 예정이에요.' },
  out: { agent: null, reply: '투자 관련 질문은 도와드리기 어려워요. 소비 분석이나 예산 조정은 바로 도와드릴 수 있어요.' },
  unknown: { agent: null, reply: '어떤 걸 도와드릴지 잘 모르겠어요. 아래에서 골라 주시거나 조금 더 자세히 적어 주세요.', showOptions: true },
}

let nextId = 1

export default function AssistantPage() {
  const [text, setText] = useState('')
  const [log, setLog] = useState([])      // { id, who: 'me' | 'bot' | 'route', ... }
  const [busy, setBusy] = useState(false)  // 라우터가 문장을 읽는 중
  const [agent, setAgent] = useState(null)  // 지금 대화 중인 도우미 (진짜로는 서버 대화 상태에 저장)
  const end = useRef(null)

  // 새 말풍선이 생기면 맨 아래로
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [log, busy])

  const add = (...items) => setLog((l) => [...l, ...items.map((m) => ({ id: nextId++, ...m }))])

  // 예산 조정 요청이 다 모였을 때 (진짜로는 예산 조정 에이전트 작업을 만든다)
  const requestAdjust = (slots) => {
    const { cat, amount, dir } = slots
    add({
      who: 'bot',
      text: `${cat}${josa(cat)} ${manwon(amount)} ${dir === 'up' ? '늘리는' : '줄이는'} 조정안을 만들게요. 나머지 카테고리는 내 우선순위에 맞춰 다시 짤게요.`,
      note: '조정안 카드는 서버가 연결되면 나와요.',
    })
  }

  // ② 고르기 말풍선에서 "조정안 받기"
  const submitForm = (formId, slots) => {
    setLog((l) => l.map((m) => (m.id === formId ? { ...m, done: true } : m)))
    add({ who: 'me', text: adjustSentence(slots) })
    requestAdjust(slots)
  }

  const send = (msg) => {
    const q = msg.trim()
    if (!q || busy) return
    setText('')
    add({ who: 'me', text: q })

    // 예산 조정 중이면 문장은 예산 조정 도우미가 먼저 받는다 (라우터를 다시 타지 않음)
    if (agent === 'budget') {
      const slots = mockSlots(q)
      if (slots.cat && slots.amount && slots.dir) return requestAdjust(slots)        // 다 있으면 바로 요청
      if (Object.keys(slots).length) {                                               // 일부만 있으면 채워서 다시 보여 주기
        return add({ who: 'bot', text: '빠진 것만 골라 주세요.' }, { who: 'form', slots })
      }
    }

    setBusy(true)
    // 진짜 라우터를 부르는 자리. 지금은 잠깐 기다리는 척만 한다
    setTimeout(() => {
      const intent = mockRoute(q).intent
      const r = ROUTED[intent]
      if (r.agent) setAgent(intent)
      add(
        ...(r.agent ? [{ who: 'route', agent: r.agent }] : []),
        { who: 'bot', text: r.reply, showOptions: r.showOptions },
        ...(r.form ? [{ who: 'form', slots: mockSlots(q) }] : []),   // ② 고르기 말풍선 (문장 속 재료로 미리 채움)
      )
      setBusy(false)
    }, 600)
  }

  const options = (
    <div className="mc-asst-opts" role="group" aria-label="추천 질문">
      {SUGGESTIONS.map((s) => (
        <button key={s.text} type="button" className={`mc-asst-opt${s.soon ? ' is-soon' : ''}`}
          disabled={s.soon} aria-disabled={s.soon || undefined} onClick={() => send(s.text)}>
          <span className="mc-asst-ic"><Icon name={s.icon} /></span>
          <span className="mc-grow">{s.text}</span>
          {s.soon ? <span className="mc-soon">준비 중</span> : <Icon name="ChevronRight" />}
        </button>
      ))}
    </div>
  )

  return (
    <Layout
      title="통합 챗봇"
      activeTab="assistant"
      foot={
        <form className="mc-chat-in mc-asst-in" onSubmit={(e) => { e.preventDefault(); send(text) }}>
          <input aria-label="챗봇에게 보낼 메시지" autoComplete="off" placeholder="예: 지난달보다 얼마나 썼어?" type="text"
            value={text} onChange={(e) => setText(e.target.value)} />
          <button type="submit" className="mc-chat-send" aria-label="보내기"><Icon name="Send" /></button>
        </form>
      }
    >
      <div className="mc-bc mc-asst">
        <div className="mc-bc-ctx mc-asst-ctx">
          <span className="mc-cat-ic mc-big-ic"><Icon name="Sparkles" /></span>
          <div>
            <em className="mc-asst-who">라우터</em>
            <strong>머니쿠션 챗봇</strong>
            <small>편하게 물어보면 맞는 도우미를 연결해 드려요</small>
          </div>
        </div>
        <div className="mc-bc-log mc-chat-log mc-asst-log" aria-live="polite">
          <div className="mc-msg bot">
            <div className="mc-bubble">안녕하세요! 무엇을 도와드릴까요?<br /><small>아래에서 고르거나 하고 싶은 말을 바로 적어 주세요.</small></div>
          </div>
          {options}

          {log.map((m) => {
            if (m.who === 'route') {
              return <div key={m.id} className="mc-asst-route"><Icon name="Link" /> {m.agent} 도우미에 연결했어요</div>
            }
            if (m.who === 'form') {
              return (
                <div key={m.id} className="mc-msg bot mc-asst-form">
                  <AdjustForm initial={m.slots} done={m.done} onSubmit={(slots) => submitForm(m.id, slots)} />
                </div>
              )
            }
            return (
              <div key={m.id} className="mc-asst-turn">
                <div className={`mc-msg ${m.who}`}>
                  <div className="mc-bubble">{m.text}{m.note && <><br /><small>{m.note}</small></>}</div>
                </div>
                {m.showOptions && options}
              </div>
            )
          })}
          {busy && <div className="mc-asst-route is-busy"><Icon name="Loader" /> 요청을 이해하는 중…</div>}
          <div ref={end} />
        </div>
      </div>
    </Layout>
  )
}
