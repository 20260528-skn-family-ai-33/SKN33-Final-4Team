// 계좌 연결 1: 은행 고르기 + 은행 계좌 조회 동의 (필수)
// 11/18 피드백: 계좌 · 카드 · 대출 연결을 화면마다 나눈다. 이 화면은 은행만, 동의도 은행 계좌 조회 하나만 맨 아래에
//   → 은행 로그인(ConnectAuthPage) → 사용할 계좌 고르기 → 카드 연결(ConnectCardPage, 선택) → 대출 정보 연결(ConnectLoanPage, 선택)
// [선택한 은행 연결하기] = 금융 조회 동의 저장(POST /api/finance/consents/) 성공 후 기관 로그인 화면으로 (11/03 S2)
// 11/12 피드백: 고를 때마다 고른 은행 · 동의 체크를 이 탭에 남긴다 (utils/connectDraft.js, 30분 · 로그아웃 시 지움)
//   → 기관 로그인에서 뒤로 와도, 내 정보로 갔다가 [저장하고 계속]으로 다시 들어와도 그대로 채워져 있다
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Mascot from '../components/Mascot.jsx'
import ChatAsk from '../components/ChatAsk.jsx'
import PickCard from '../components/PickCard.jsx'
import ScopeConsent, { saveScopeConsent, useScopeConsent } from '../components/ScopeConsent.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'
import { BANKS, EXTRA, NOT_SUPPORTED } from '../utils/institutions.js'
import { readConnectDraft, saveConnectDraft } from '../utils/connectDraft.js'

export default function ConnectPage() {
  const navigate = useNavigate()
  const [draft] = useState(readConnectDraft)  // 처음 그릴 때 한 번만 읽는다 (없으면 null)
  const banks = usePicked(draft?.banks)
  const [busy, setBusy] = useState(false)
  const consent = useScopeConsent('bank', draft?.agreed)
  // 바뀔 때마다 이 탭에 남긴다 (agreedKeys 는 매번 새 배열이라 글자로 바꿔 비교)
  const snapshot = JSON.stringify({ banks: banks.list, agreed: consent.agreedKeys() })
  useEffect(() => { saveConnectDraft(JSON.parse(snapshot)) }, [snapshot])
  const [error, setError] = useState('')

  const notes = BANKS.filter((x) => banks.has(x.name) && EXTRA[x.name]?.note).map((x) => [x.name, EXTRA[x.name].note])

  const next = async () => {
    if (busy) return
    if (banks.list.length === 0) return setError('연결할 은행을 1곳 이상 골라 주세요.')
    if (!consent.docs) return setError('동의 문서를 불러온 뒤 동의해 주세요.')
    if (!consent.allAgreed) return setError('은행 계좌 정보 조회는 꼭 동의해야 해요.')
    setBusy(true)
    try {
      await saveScopeConsent(consent)
    } catch (err) {
      setBusy(false)
      // 화면을 연 사이 약관이 바뀜 → 새 문서를 받고 처음부터 다시 확인받는다 (체크는 지워진다)
      if (err.code === 'document_outdated') consent.reload()
      return setError(err.message)
    }
    // 고른 은행을 다음 화면(기관 로그인)으로 넘긴다. 화면 순서대로
    navigate(PATHS.connectAuth, { state: { banks: BANKS.filter((b) => banks.has(b.name)).map((b) => b.name), cards: [] } })
  }

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" disabled={busy} onClick={next}>{busy ? '동의 저장 중…' : '선택한 은행 연결하기'}</button>}
    >
      <StepBar step={2} />
      <div className="mc-chat">
        <div className="mc-o2-line">
          <Mascot mood="planner" size={64} />
          <p className="mc-o2-bubble">먼저 <b>월급이 들어오는 은행</b>을 연결해요. 사용할 계좌는 다음 단계에서 골라요.</p>
        </div>

        <PickCard title="어느 은행을 쓰나요?" hint="여러 곳 가능" items={BANKS} picked={banks} moreLabel="준비 중인 은행" soon={NOT_SUPPORTED.bank} className="mc-pick-bank" />

        {/* 고른 은행 중 미리 알아 둘 것이 있는 곳 (농협 조회계좌 등) */}
        {notes.length > 0 && (
          <div className="mc-card mc-chat-answer mc-inst-notes">
            <h2>연결 전에 확인해 주세요</h2>
            <ul>{notes.map(([name, note]) => <li key={name}>{note.startsWith(name) ? note : <><b>{name}</b> {note}</>}</li>)}</ul>
          </div>
        )}

        <ChatAsk>계좌 정보 조회에 동의해 주세요 <span className="mc-opt">필수</span></ChatAsk>
        <ScopeConsent consent={consent} onChange={() => setError('')} />
        {error && <div className="mc-error" role="alert">{error}</div>}
      </div>
    </Layout>
  )
}
