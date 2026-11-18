// 카드 연결 (선택, 11/18): 계좌를 고른 다음 화면. [카드 연결하기]를 누른 사람에게만 카드사 고르기 + 카드 정보 조회 동의를 보여 준다
// [건너뛰기] = 동의를 받지 않고 대출 정보 연결로. 연결하면 카드사 로그인(ConnectAuthPage, kind 'card') → 사용할 카드 고르기 → 대출 정보 연결
// 연결 관리에서 왔으면(state.from === 'manage') 처음부터 고르기를 펼치고, 끝나면 연결 관리로 돌아간다 (단계 표시줄 없음)
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Mascot from '../components/Mascot.jsx'
import ChatAsk from '../components/ChatAsk.jsx'
import PickCard from '../components/PickCard.jsx'
import ScopeConsent, { saveScopeConsent, useScopeConsent } from '../components/ScopeConsent.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'
import { CARDS, EXTRA, NOT_SUPPORTED } from '../utils/institutions.js'

export default function ConnectCardPage() {
  const navigate = useNavigate()
  const manage = useLocation().state?.from === 'manage'
  const [connect, setConnect] = useState(manage)  // [카드 연결하기]를 눌렀는지
  const cards = usePicked([])
  const consent = useScopeConsent('card')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const notes = CARDS.filter((x) => cards.has(x.name) && EXTRA[x.name]?.note).map((x) => [x.name, EXTRA[x.name].note])

  const next = async () => {
    if (busy) return
    if (cards.list.length === 0) return setError('연결할 카드사를 1곳 이상 골라 주세요.')
    if (!consent.docs) return setError('동의 문서를 불러온 뒤 동의해 주세요.')
    if (!consent.allAgreed) return setError('카드를 연결하려면 카드 정보 조회에 동의해 주세요.')
    setBusy(true)
    try {
      await saveScopeConsent(consent)
    } catch (err) {
      setBusy(false)
      if (err.code === 'document_outdated') consent.reload()
      return setError(err.message)
    }
    navigate(PATHS.connectAuth, { state: { banks: [], cards: CARDS.filter((c) => cards.has(c.name)).map((c) => c.name), kind: 'card', from: manage ? 'manage' : undefined } })
  }

  const foot = connect
    ? <button type="button" className="mc-primary" disabled={busy} onClick={next}>{busy ? '동의 저장 중…' : '선택한 카드사 연결하기'}</button>
    : <>
      <button type="button" className="mc-primary" onClick={() => setConnect(true)}>카드 연결하기</button>
      <button type="button" className="mc-o3-ghost" onClick={() => navigate(PATHS.connectLoan)}>건너뛰기</button>
    </>

  return (
    <Layout title="카드 연결" showBack foot={foot}>
      {!manage && <StepBar step={2} />}
      <div className="mc-chat">
        <div className="mc-o2-line">
          <Mascot mood="shopping" size={64} />
          <p className="mc-o2-bubble">카드를 연결하면 <b>카드 결제</b>와 청구서도 예산에 반영해요. 나중에 연결해도 돼요.</p>
        </div>
        {connect && <>
          <PickCard title="어느 카드사를 쓰나요?" hint="여러 곳 가능" items={CARDS} picked={cards} moreLabel="다른 카드사" notSupported={NOT_SUPPORTED.card} />
          {notes.length > 0 && (
            <div className="mc-card mc-chat-answer mc-inst-notes">
              <h2>연결 전에 확인해 주세요</h2>
              <ul>{notes.map(([name, note]) => <li key={name}>{note.startsWith(name) ? note : <><b>{name}</b> {note}</>}</li>)}</ul>
            </div>
          )}
          <ChatAsk>카드 정보 조회에 동의해 주세요</ChatAsk>
          <ScopeConsent consent={consent} onChange={() => setError('')} />
        </>}
        {error && <div className="mc-error" role="alert">{error}</div>}
      </div>
    </Layout>
  )
}
