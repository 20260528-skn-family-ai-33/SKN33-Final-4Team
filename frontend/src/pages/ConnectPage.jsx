// 계좌 연결 1: 은행·카드사 고르기
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'
import { BANKS, CARDS, EXTRA, NOT_SUPPORTED } from '../utils/institutions.js'
import { usePopup } from '../popups/context.js'
import TermsPopup from '../popups/TermsPopup.jsx'
import { FINANCE_TERMS, TERMS } from '../popups/termsText.js'

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
  const [hasLoan, setHasLoan] = useState(null)
  const banks = usePicked(['신한은행', '국민은행'])
  const cards = usePicked(['신한카드'])
  const { openPopup } = usePopup()
  // 동의 3개: 계좌 필수 · 카드 선택 · 대출 선택 { financeBank: true/false, ... }
  const [consent, setConsent] = useState(() => Object.fromEntries(FINANCE_TERMS.map((k) => [k, false])))
  const [error, setError] = useState('')
  const allConsent = FINANCE_TERMS.every((k) => consent[k])
  const setOne = (k, on) => { setConsent((c) => ({ ...c, [k]: on })); setError('') }
  const setAll = (on) => { setConsent(Object.fromEntries(FINANCE_TERMS.map((k) => [k, on]))); setError('') }
  // 팝업 안 "동의하고 닫기"를 누르면 그 동의가 체크된다
  const openConsent = (k) => openPopup(<TermsPopup kind={k} onAgree={() => setOne(k, true)} />)

  const notes = [...BANKS, ...CARDS]
    .filter((x) => (banks.has(x.name) || cards.has(x.name)) && (EXTRA[x.name]?.note || !x.idLogin))
    .map((x) => [x.name, x.idLogin ? EXTRA[x.name].note : '공동인증서로만 연결돼요. (공동인증서 로그인은 준비 중)'])

  const next = () => {
    if (banks.list.length === 0) return setError('연결할 은행을 1곳 이상 골라 주세요.')
    if (!consent.financeBank) return setError('은행 계좌 정보 조회는 꼭 동의해야 해요.')
    if (cards.list.length > 0 && !consent.financeCard) return setError('카드를 연결하려면 카드 정보 조회에 동의해 주세요. 카드는 나중에 연결해도 돼요.')
    navigate(PATHS.connectAuth, {
      // 고른 기관을 다음 화면(기관 로그인)으로 넘긴다. 화면 순서대로
      // consent: 다음 단계에서 대출을 불러올지 등에 쓴다 (저장은 API 연결 후 동의 기록 consent 테이블에 목적별로)
      state: {
        banks: BANKS.filter((b) => banks.has(b.name)).map((b) => b.name),
        cards: CARDS.filter((c) => cards.has(c.name)).map((c) => c.name),
        consent,
      },
    })
  }

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" onClick={next}>선택한 기관 연결하기</button>}
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
        <label className="mc-field mc-check mc-terms-all">
          <input type="checkbox" checked={allConsent} onChange={(e) => setAll(e.target.checked)} />금융 정보 조회 전체 동의
        </label>
        {FINANCE_TERMS.map((k) => (
          <div className="mc-terms-row" key={k}>
            <input aria-label={TERMS[k].label} checked={consent[k]} id={`mc-consent-${k}`} type="checkbox"
              onChange={(e) => setOne(k, e.target.checked)} />
            <button type="button" className="mc-terms-open" aria-haspopup="dialog" onClick={() => openConsent(k)}>
              {TERMS[k].label}<span>보기</span>
            </button>
          </div>
        ))}
        <p className="mc-picked mc-consent-note">선택 동의를 하지 않아도 계좌로 예산을 짤 수 있어요. 대출에 동의하지 않으면 대출 정보는 불러오지 않아요.</p>
      </div>
      <p className="mc-note">시안에서는 실제 인증 정보나 계좌 정보를 받지 않아요.</p>
      {error && <div className="mc-error" role="alert">{error}</div>}
    </Layout>
  )
}
