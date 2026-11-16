// 연결 관리 — 마이 > 연결 관리 (11/03 S2: 시안 예시값 → 실제 API)
// 연결 GET /api/finance/connections/ · 계좌 GET /api/finance/connect/accounts/ · 카드 GET /api/finance/connect/cards/ · 대출 GET /api/finance/loans/
// relogin = 다시 연결 필요 → 기관 로그인 팝업으로 POST /api/finance/connections/ (같은 연결 행을 되살림)
// [다시 불러오기] = POST /api/finance/sync/ (그 연결만). 현대카드처럼 카드 값이 필요하면 그 자리에서 받아 한 번만 보낸다
// 계좌 · 카드 · 대출 목록은 영역마다 따로 보여 준다. 카드 · 대출 조회 동의는 선택이라 403 이어도 은행 관리는 그대로 쓴다
// 대출 항목은 상환 정보 수정으로, 직접 등록은 저장 후 목록 새로고침으로 연결한다.
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import ConnectLoginPopup from '../popups/ConnectLoginPopup.jsx'
import DisconnectPopup from '../popups/DisconnectPopup.jsx'
import AccountPopup from '../popups/AccountPopup.jsx'
import LoanAddPopup from '../popups/LoanAddPopup.jsx'
import { useApi } from '../utils/api.js'
import { runFinanceSync, syncLines } from '../utils/financeSync.js'
import { cardPw } from '../utils/institutions.js'
import { reconnect } from '../utils/reconnect.jsx'
import CardNoInput from '../components/CardNoInput.jsx'
import { won } from '../utils/money.js'
import { splitName } from '../utils/transactions.js'
import { PATHS } from '../routes.js'

const when = (iso) => (iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '조회 기록 없음')
const items = (q) => q.data?.items ?? []
// 계좌 · 카드 한 줄: 이름은 한 줄 말줄임, 끝 4자리는 아래 줄 앞에 (11/9: 긴 이름이 줄바꿈되며 모양이 깨져서)
//   누르면 상세 팝업 (AccountPopup)
//   아래 줄: '****0303 · 관리 중' 다음 줄에 마지막 조회 시각
function AssetRow({ icon, displayName, sub, fetchedAt, right, onOpen }) {
  const [name, last4] = splitName(displayName)
  return (
    <button type="button" className="mc-listrow" onClick={onOpen}>
      <span className="mc-tile"><Icon name={icon} /></span>
      <span className="mc-grow">
        <strong className="mc-tx-name"><span>{name}</span></strong>
        <span className="mc-muted mc-asset-sub">{[last4, sub].filter(Boolean).join(' · ')}</span>
        <span className="mc-muted mc-asset-sub">{when(fetchedAt)}{fetchedAt ? ' 조회' : ''}</span>
      </span>
      <span className="mc-right mc-tx-amt">{right != null && <>{right} </>}<span className="mc-muted">›</span></span>
    </button>
  )
}

export default function ConnectionsPage() {
  const conns = useApi('/api/finance/connections/')
  const accts = useApi('/api/finance/connect/accounts/')
  const cards = useApi('/api/finance/connect/cards/')
  const loans = useApi('/api/finance/loans/')
  const reviews = useApi('/api/finance/card-reviews/')
  // 연결 해제 때 같이 그만할지 물을 고정지출 · 정기 저축 (결정 F)
  const fixed = useApi('/api/budgets/fixed/')
  const savings = useApi('/api/budgets/savings/')
  if (!conns.data) return <Layout title="연결 관리" showBack><ApiStatus error={conns.error} /></Layout>
  const reload = () => [conns, accts, cards, loans, fixed, savings, reviews].forEach((x) => x.reload())
  return <Connections conns={conns.data.items} accts={accts} cards={cards} loans={loans} reviews={reviews} fixed={fixed} savings={savings} reload={reload} />
}

// 영역 목록을 못 받았을 때의 문장. 동의 안 함 · 오류를 '항목 없음'으로 보이지 않는다
function ListStatus({ q, what }) {
  if (q.data) return null
  if (!q.error) return <p className="mc-muted">불러오는 중…</p>
  if (q.error.code === 'consent_required') return <p className="mc-muted">{what} 조회 동의가 필요해요. 연결 화면에서 동의하면 여기에 보여 드려요.</p>
  return (
    <p className="mc-error" role="alert">{what} 목록을 못 불러왔어요. ({q.error.status ? q.error.message : '서버에 연결하지 못했어요.'}){' '}
      <button type="button" className="mc-link" onClick={q.reload}>다시 시도</button></p>
  )
}

// 결정 F: 이 연결의 계좌 · 카드(accountIds · cardIds)로 나가는 확정 고정지출 · 정기 저축 → DisconnectPopup 의 related 모양
function relatedTo(c, fixed, savings) {
  const ids = new Set([...(c.accountIds ?? []), ...(c.cardIds ?? [])])
  const line = (day, method) => `${day ? `매월 ${day}일` : '날짜 모름'} · ${method.displayName}`
  return [
    ...items(fixed).filter((f) => f.status === 'confirmed' && ids.has(f.paymentAccount?.accountId ?? f.paymentCard?.cardId))
      .map((f) => ({ id: f.recurringId, type: 'fixed', name: f.name, amount: f.amount, line: line(f.paymentDay, f.paymentAccount ?? f.paymentCard) })),
    ...items(savings).filter((s) => s.status === 'confirmed' && ids.has(s.fromAccount?.accountId))
      .map((s) => ({ id: s.savingId, type: 'saving', name: s.name, amount: s.amount, line: line(s.transferDay, s.fromAccount) })),
  ]
}

function Connections({ conns, accts, cards, loans, reviews, fixed, savings, reload }) {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  // 연결별 진행 상태 { [connectionId]: { busy, lines, error, needCard } }
  const [work, setWork] = useState({})
  const [cardForm, setCardForm] = useState({ cardNo: '', cardPassword: '' })
  const setOne = (id, patch) => setWork((w) => ({ ...w, [id]: { ...w[id], ...patch } }))
  const banks = conns.filter((c) => c.businessType === 'bank')
  const cardConns = conns.filter((c) => c.businessType === 'card')

  const refresh = async (c, credential) => {
    setOne(c.connectionId, { busy: true, error: '', lines: null, needCard: false })
    try {
      const body = { connectionIds: [c.connectionId] }
      if (credential) body.cardCredentials = [{ connectionId: c.connectionId, ...credential }]
      const result = await runFinanceSync(body)
      const item = result.items?.[0]
      const needCard = Boolean(item?.status === 'failed' && item.error?.fields && ('cardNo' in item.error.fields || 'cardPassword' in item.error.fields))
      setOne(c.connectionId, { busy: false, lines: syncLines(result), needCard })
      reload()
    } catch (err) {
      setOne(c.connectionId, { busy: false, error: err.message })
    }
  }

  // 다시 연결: 기관 로그인 팝업을 그대로 다시 쓴다
  const relogin = (c) => openPopup(
    <ConnectLoginPopup inst={c.organizationName} onSubmit={async (form) => {
      setOne(c.connectionId, { busy: true, error: '', lines: null })
      // 문자 인증을 요구하면 그 자리에서 문자 인증 팝업 (utils/reconnect.jsx, 11/14)
      const r = await reconnect(openPopup, c, form)
      setOne(c.connectionId, {
        busy: false,
        error: r.status === 'connected' ? '' : r.error?.message ?? '다시 연결하지 못했어요.',
        lines: r.status === 'connected' ? [{ name: c.organizationName, ok: r.syncStatus !== 'failed', text: r.syncStatus === 'failed' ? '연결됐지만 내역을 불러오지 못했어요.' : '다시 연결하고 내역을 불러왔어요.' }] : null,
      })
      reload()
    }} />,
  )

  // 계좌 · 카드 상세: 그 하나로 나가는 고정지출 · 저축만 (relatedTo 를 한 장짜리로)
  const openAsset = (kind, item, orgName) => openPopup(
    <AccountPopup kind={kind} item={item} orgName={orgName}
      related={!fixed.data || !savings.data ? null : relatedTo(kind === 'card' ? { cardIds: [item.cardId] } : { accountIds: [item.accountId] }, fixed, savings)} />,
  )

  const disconnect = (c) => openPopup(
    <DisconnectPopup inst={c.organizationName} kind={c.businessType === 'card' ? 'card' : 'bank'} connectionId={c.connectionId} onDone={reload}
      related={relatedTo(c, fixed, savings)} relatedMissing={!fixed.data || !savings.data} />,
  )

  const head = (c) => {
    const w = work[c.connectionId] ?? {}
    return (
      <>
        {/* 기관 한 줄: 이름 · 상태 · 마지막 인증 | 작은 [다시 불러오기] (11/9: 큰 버튼 두 개가 계좌 목록보다 눈에 띄어서) */}
        <div className="mc-conn-head">
          <span className="mc-grow">
            <strong>{c.organizationName}</strong>{' '}
            {c.status === 'relogin' ? <span className="mc-conn-fail">다시 연결 필요</span> : <span className="ok">연결됨</span>}
            <small>마지막 인증 {when(c.lastAuthenticatedAt)}</small>
          </span>
          {c.status === 'relogin'
            ? <button type="button" className="mc-conn-act is-alert" disabled={w.busy} onClick={() => relogin(c)}>다시 연결</button>
            : <button type="button" className="mc-conn-act" disabled={w.busy} aria-busy={w.busy} onClick={() => refresh(c)}>
              <span className={w.busy ? 'mc-conn-busy' : undefined}><Icon name="RefreshCw" size={13} /></span>{w.busy ? '불러오는 중' : '다시 불러오기'}
            </button>}
        </div>
        {w.lines?.map((l) => <p key={l.name} className={l.ok ? 'mc-note' : 'mc-error'}>{l.text}</p>)}
        {w.error && <p className="mc-error" role="alert">{w.error}</p>}
        {w.needCard && (
          <form className="mc-form" onSubmit={(e) => { e.preventDefault(); refresh(c, cardForm); setCardForm({ cardNo: '', cardPassword: '' }) }}>
            <label className="mc-field">카드번호<CardNoInput value={cardForm.cardNo}
              onChange={(cardNo) => setCardForm((f) => ({ ...f, cardNo }))} /></label>
            <label className="mc-field">카드 비밀번호{cardPw(c.organizationCode).hint}<input autoComplete="off" type="password" inputMode="numeric" maxLength={cardPw(c.organizationCode).maxLength} value={cardForm.cardPassword}
              onChange={(e) => setCardForm((f) => ({ ...f, cardPassword: e.target.value.replace(/\D/g, '') }))} /></label>
            <button type="submit" className="mc-secondary">카드 값 넣고 다시 불러오기</button>
            <p className="mc-note">카드번호 · 카드 비밀번호는 이번 요청에만 쓰고 저장하지 않아요.</p>
          </form>
        )}
      </>
    )
  }

  return (
    <Layout title="연결 관리" showBack>
      <h1>연결 관리</h1>
      <div className="mc-card">
        <div className="mc-row"><h2>은행 {banks.length}곳{accts.data && ` · 계좌 ${accts.data.items.length}개`}</h2><span className="mc-badge">필수 · 조회 전용</span></div>
        {banks.length === 0 && <p className="mc-muted">연결된 은행이 없어요.</p>}
        {banks.length > 0 && <ListStatus q={accts} what="계좌" />}
        {banks.map((c) => (
          <div key={c.connectionId}>
            {head(c)}
            {items(accts).filter((a) => a.connectionId === c.connectionId).map((a) => (
              <AssetRow key={a.accountId} icon="Landmark" displayName={a.displayName}
                sub={a.isSelected ? '관리 중' : '고르지 않음'} fetchedAt={a.fetchedAt}
                right={a.balance == null ? '잔액 모름' : won(a.balance)} onOpen={() => openAsset('account', a, c.organizationName)} />
            ))}
            {/* 11/9: [해제]는 기관 줄 [다시 불러오기] 옆에서 빼 그 기관 목록 맨 아래 오른쪽으로 (실수로 누르기 어렵게, 눈에 덜 띄게) */}
            <div className="mc-conn-unlink"><button type="button" onClick={() => disconnect(c)}>{c.organizationName} 연결 해제</button></div>
          </div>
        ))}
        <div className="mc-inline mc-conn-foot">
          <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connectAccounts, { state: { from: 'manage' } })}>계좌 선택 바꾸기</button>
          <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connect)}>+ 은행 추가</button>
        </div>
      </div>

      <div className="mc-card">
        <div className="mc-row"><h2>카드사 {cardConns.length}곳{cards.data && ` · 카드 ${cards.data.items.length}장`}</h2><span className="mc-hint">선택</span></div>
        <ListStatus q={cards} what="카드" />
        <ListStatus q={reviews} what="결제 카드 확인 거래" />
        {items(reviews).length > 0 && <button className="mc-secondary" type="button" onClick={() => navigate(PATHS.cardReviews)}>
          결제 카드를 확인할 거래 {items(reviews).length}건
        </button>}
        {cardConns.map((c) => (
          <div key={c.connectionId}>
            {head(c)}
            {items(cards).filter((k) => c.cardIds.includes(k.cardId)).map((k) => (
              <AssetRow key={k.cardId} icon="CreditCard" displayName={k.displayName}
                sub={`${k.isSelected ? '관리 중' : '고르지 않음'}${k.cardType ? ` · ${k.cardType}` : ''}`} fetchedAt={k.fetchedAt} onOpen={() => openAsset('card', k, c.organizationName)} />
            ))}
            {/* 11/9: [해제]는 기관 줄 [다시 불러오기] 옆에서 빼 그 기관 목록 맨 아래 오른쪽으로 (실수로 누르기 어렵게, 눈에 덜 띄게) */}
            <div className="mc-conn-unlink"><button type="button" onClick={() => disconnect(c)}>{c.organizationName} 연결 해제</button></div>
          </div>
        ))}
        <div className="mc-inline mc-conn-foot">
          {cards.data && <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connectCards)}>카드 선택 바꾸기</button>}
          <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connect)}>+ 카드사 추가</button>
        </div>
      </div>

      <div className="mc-card">
        <div className="mc-row"><h2>대출{loans.data && ` ${loans.data.items.length}건`}</h2><span className="mc-hint">상환 중 · 조회 전용</span></div>
        <ListStatus q={loans} what="대출" />
        {loans.data?.hasInquiryConsent === false && <p className="mc-note">직접 등록한 대출만 표시하고 있어요. 기관 대출도 불러오려면 <button type="button" className="mc-link" onClick={() => navigate(PATHS.connect)}>대출 조회에 동의해 주세요</button>.</p>}
        {items(loans).map((l) => (
          <button type="button" className="mc-listrow" key={l.loanId} onClick={() => navigate(PATHS.loanCheck, { state: { from: 'manage', loanId: l.loanId } })}>
            <span className="mc-tile"><Icon name="Landmark" /></span>
            <span className="mc-grow"><strong>{l.productName ?? '대출'}</strong><span className="mc-muted">{l.lenderName ?? '기관 확인 필요'} · {l.origin === 'manual' ? '직접 등록' : '은행 조회'}</span></span>
            <span className="mc-right">{(l.origin === 'manual' ? l.manualBalance : l.reportedBalance) == null ? '잔액 확인 필요' : won(l.origin === 'manual' ? l.manualBalance : l.reportedBalance)} ›</span>
          </button>
        ))}
        {/* 11/9: 은행 · 카드사 칸과 같은 아래 버튼. 등록 뒤엔 목록에 새 줄이 생기므로 따로 안내하지 않는다 */}
        {(loans.data || loans.error?.code === 'consent_required') && <div className="mc-conn-foot">
          <button type="button" className="mc-secondary" onClick={() => openPopup(<LoanAddPopup onSaved={() => loans.reload()} />)}>+ 대출 직접 등록</button>
        </div>}
      </div>
      <p className="mc-note">송금·결제는 실행하지 않아요. 연결을 해제해도 지금까지 불러온 거래는 남아요.</p>
    </Layout>
  )
}
