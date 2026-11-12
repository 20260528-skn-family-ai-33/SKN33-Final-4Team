// 계좌 연결 1: 은행·카드사 고르기
// [선택한 기관 연결하기] = 금융 조회 동의 저장(POST /api/finance/consents/) 성공 후 기관 로그인 화면으로 (11/03 S2)
// 11/12 피드백: 다음 화면으로 가기 전에 고른 것(대출 답 · 은행 · 카드사 · 동의 체크)을 이 화면의 방문 기록(location.state.draft)에 남긴다
//   → 뒤로 가기로 돌아오면 그대로 채워져 있다. 서버에 저장하지 않는 값이라 새로 들어오면 빈 화면부터
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'
import { BANKS, CARDS, EXTRA, NOT_SUPPORTED } from '../utils/institutions.js'
import { usePopup } from '../popups/context.js'
import TermsPopup from '../popups/TermsPopup.jsx'
import { useConsentDocs } from '../popups/consentDocs.js'
import { apiSend, newRequestKey } from '../utils/api.js'

const LOAN_ANSWERS = ['있어요', '없어요', '나중에 확인']

// 칩 여러 개 + "다른 ○○ 보기" + "2곳 선택 · 신한, 국민" 을 한 덩어리로
// items = utils/institutions.js 의 BANKS · CARDS. 자주 쓰는 곳(popular)만 먼저 보이고 나머지는 펼친다
function PickCard({ title, hint, items, picked, moreLabel, notSupported = [] }) {
  const [open, setOpen] = useState(false)
  // 펼치지 않았어도 이미 고른 기관은 보이게
  const shown = items.filter((x) => open || x.popular || picked.has(x.name))
  const rest = items.length - items.filter((x) => x.popular).length

  return (
    <div className="mc-card">
      <h2>{title} <span className="mc-hint">{hint}</span></h2>
      <div className="mc-chipbar">
        {shown.map((x) => (
          <button key={x.name} type="button" aria-pressed={picked.has(x.name)} onClick={() => picked.toggle(x.name)}>
            {x.label}{!x.idLogin && <small className="mc-chip-cert">인증서만</small>}
          </button>
        ))}
      </div>
      <button type="button" className="mc-link mc-pick-more" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? '접기 ∧' : `${moreLabel} ${rest}곳 더 보기 ∨`}
      </button>
      {open && items.some((x) => !x.idLogin) && <p className="mc-picked">'인증서만'은 공동인증서로만 연결돼요.</p>}
      {notSupported.length > 0 && <p className="mc-picked">{notSupported.join('·')}는 아직 지원하지 않아요.</p>}
      {/* 고른 순서가 아니라 화면 순서대로 이름을 보여 준다 */}
      <p className="mc-picked"><b>{picked.list.length}곳</b> 선택 · {items.filter((x) => picked.has(x.name)).map((x) => x.label).join(', ')}</p>
    </div>
  )
}

export default function ConnectPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const draft = location.state?.draft  // 뒤로 와서 다시 그릴 때만 있다
  const [hasLoan, setHasLoan] = useState(draft?.hasLoan ?? null)
  const banks = usePicked(draft?.banks)
  const cards = usePicked(draft?.cards)
  const [busy, setBusy] = useState(false)
  const { openPopup } = usePopup()
  // 동의 3개(서버 문서): 계좌 필수 · 카드 선택 · 대출 선택 — docs/api/finance-connect.md '금융 조회 동의 저장'
  const consent = useConsentDocs((d) => d.purpose === 'finance_inquiry', draft?.agreed)
  const [error, setError] = useState('')
  const agreedTo = (scope) => consent.docs?.some((d) => d.productScope === scope && consent.isAgreed(d))
  const setOne = (d, on) => { consent.setOne(d, on); setError('') }
  const setAll = (on) => { consent.setAll(on); setError('') }
  // 팝업 안 "동의하고 닫기"를 누르면 그 동의가 체크된다
  const openConsent = (d) => openPopup(<TermsPopup doc={d.document} onAgree={() => setOne(d, true)} />)

  const notes = [...BANKS, ...CARDS]
    .filter((x) => (banks.has(x.name) || cards.has(x.name)) && (EXTRA[x.name]?.note || !x.idLogin))
    .map((x) => [x.name, x.idLogin ? EXTRA[x.name].note : '공동인증서로만 연결돼요. (공동인증서 로그인은 준비 중)'])

  const next = async () => {
    if (busy) return
    if (banks.list.length === 0) return setError('연결할 은행을 1곳 이상 골라 주세요.')
    if (!consent.docs) return setError('동의 문서를 불러온 뒤 동의해 주세요.')
    if (!agreedTo('bank')) return setError('은행 계좌 정보 조회는 꼭 동의해야 해요.')
    if (cards.list.length > 0 && !agreedTo('card')) return setError('카드를 연결하려면 카드 정보 조회에 동의해 주세요. 카드는 나중에 연결해도 돼요.')
    setBusy(true)
    try {
      // 같은 범위 · 같은 문서 버전의 동의가 이미 있으면 서버가 새로 만들지 않는다 (N⑥)
      await apiSend('/api/finance/consents/', 'POST', {
        // 화면에 보여 준 버전 그대로. 해시는 서버가 그 버전의 원문으로 만든다
        consents: consent.docs.filter(consent.isAgreed).map((d) => ({ purpose: d.purpose, productScope: d.productScope, documentVersion: d.version })),
      }, { requestKey: newRequestKey() })
    } catch (err) {
      setBusy(false)
      // 화면을 연 사이 약관이 바뀜 → 새 문서를 받고 처음부터 다시 확인받는다 (체크는 지워진다)
      if (err.code === 'document_outdated') consent.reload()
      return setError(err.message)
    }
    // 뒤로 왔을 때 다시 채울 수 있게 지금 고른 것을 이 화면 기록에 남긴다 (주소는 그대로, 기록만 바꿈)
    navigate(location.pathname, { replace: true, state: { ...location.state, draft: {
      hasLoan, banks: banks.list, cards: cards.list, agreed: consent.agreedKeys(),
    } } })
    // 고른 기관을 다음 화면(기관 로그인)으로 넘긴다. 화면 순서대로
    navigate(PATHS.connectAuth, {
      state: {
        banks: BANKS.filter((b) => banks.has(b.name)).map((b) => b.name),
        cards: CARDS.filter((c) => cards.has(c.name)).map((c) => c.name),
      },
    })
  }

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" disabled={busy} onClick={next}>{busy ? '동의 저장 중…' : '선택한 기관 연결하기'}</button>}
    >
      <StepBar step={2} />
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>연결할 은행을 골라 주세요</h1>
      <p className="mc-muted">기관 인증 후 내 계좌 목록을 불러와요. 사용할 계좌는 다음 단계에서 골라요.</p>

      <div className="mc-card mc-loanq">
        <h2>은행 밖에서 받은 대출도 있나요?</h2>
        <div className="mc-seg mc-seg-sm mc-loanq-seg" style={{ marginTop: 8 }}>
          {LOAN_ANSWERS.map((x) => <button key={x} type="button" aria-pressed={hasLoan === x} onClick={() => setHasLoan(x)}>{x}</button>)}
        </div>
        <p className="mc-picked">은행 대출은 은행을 연결하면 자동으로 불러와요. 학자금·캐피탈처럼 은행 밖 대출이 있으면 '있어요'를 골라 주세요.</p>
      </div>

      <PickCard title="연결할 은행" hint="필수 · 복수 선택" items={BANKS} picked={banks} moreLabel="다른 은행" notSupported={NOT_SUPPORTED} />
      <PickCard title="카드사" hint="선택 · 나중에 해도 돼요" items={CARDS} picked={cards} moreLabel="다른 카드사" />

      {/* 고른 기관 중 미리 알아 둘 것이 있는 곳 (농협 조회계좌 · 씨티/롯데 문자 인증 · 대구 출금계좌 등) */}
      {notes.length > 0 && (
        <div className="mc-card mc-inst-notes">
          <h2>연결 전에 확인해 주세요</h2>
          <ul>{notes.map(([name, note]) => <li key={name}>{note.startsWith(name) ? note : <><b>{name}</b> {note}</>}</li>)}</ul>
        </div>
      )}

      {/* 동의: 체크박스 + 이름(누르면 약관 상세 팝업) — 회원가입 약관과 같은 모양 */}
      <div className="mc-card mc-consent-card">
        {consent.error ? (
          <div role="alert">
            <p className="mc-error">동의 문서를 불러오지 못했어요. 문서를 확인해야 연결할 수 있어요.</p>
            <button type="button" className="mc-link" onClick={consent.reload}>다시 불러오기</button>
          </div>
        ) : !consent.docs ? (
          <p className="mc-muted">동의 문서를 불러오는 중…</p>
        ) : (
          <>
            <label className="mc-field mc-check mc-terms-all">
              <input type="checkbox" checked={consent.allAgreed} onChange={(e) => setAll(e.target.checked)} />금융 정보 조회 전체 동의
            </label>
            {consent.docs.map((d) => (
              <div className="mc-terms-row" key={d.productScope}>
                <input aria-label={d.document.label} checked={consent.isAgreed(d)} id={`mc-consent-${d.productScope}`} type="checkbox"
                  onChange={(e) => setOne(d, e.target.checked)} />
                <button type="button" className="mc-terms-open" aria-haspopup="dialog" onClick={() => openConsent(d)}>
                  {d.document.label}<span>보기</span>
                </button>
              </div>
            ))}
          </>
        )}
        <p className="mc-picked mc-consent-note">선택 동의를 하지 않아도 계좌로 예산을 짤 수 있어요. 대출에 동의하지 않으면 대출 정보는 불러오지 않아요.</p>
      </div>
      {error && <div className="mc-error" role="alert">{error}</div>}
    </Layout>
  )
}
