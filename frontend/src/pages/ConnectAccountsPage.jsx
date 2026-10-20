// 계좌 연결 3: 사용할 계좌 고르기
// 계좌 목록은 시안 예시값 — CODEF 보유계좌 조회 API 가 생기면 바꾼다
// 실제 응답처럼: 이름 = 별칭(resAccountNickName)이 있으면 별칭, 없으면 상품명(resAccountName)
//               종류 = 예금구분(resAccountDeposit) 11 입출금 · 12 적금 · 10/14 미분류 → 미분류는 사용자가 고른다
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import AcctPick from '../components/AcctPick.jsx'
import { usePicked } from '../utils/usePicked.js'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const USES = ['생활비', '고정지출', '저축', '쿠션']
const KIND_PICK = [['입출금', '입출금'], ['적금', '적금·예금']]  // 미분류 계좌에서 고르는 값 [값, 버튼 글자]
const SAVING_NOTE = '저축 계좌예요. 생활비 잔액에는 더하지 않아요.'
const BANKS = [
  { bank: '신한은행', accounts: [
    { id: 'sh1', product: '저축예금', nickname: null, no: '1234', kind: '입출금', balance: 340_000, use: '생활비' },
    { id: 'sh2', product: '신한 자유적금', nickname: null, no: '7890', kind: '적금', balance: 1_200_000 },
  ] },
  { bank: '국민은행', accounts: [
    { id: 'kb1', product: 'KB국민ONE통장', nickname: '급여통장', no: '5678', kind: '입출금', balance: 2_460_000, use: '고정지출' },
    { id: 'kb3', product: 'KB마이핏통장', nickname: null, no: '9012', kind: null, balance: 150_000 },  // 예금구분 미분류
    { id: 'kb2', product: '직장인 마이너스통장', nickname: null, no: '3344', kind: '마이너스통장', balance: -150_000,
      note: '한도는 은행에서 확인되지 않았어요. 마이너스 금액은 내 돈이 아니라서 잔액에 더하지 않아요.' },
  ] },
]
const ALL = BANKS.flatMap((b) => b.accounts)
// 입출금 계좌는 처음부터 골라 둔다
const FIRST_PICKED = ALL.filter((a) => a.use).map((a) => a.id)
const FIRST_USES = Object.fromEntries(ALL.filter((a) => a.use).map((a) => [a.id, a.use]))

export default function ConnectAccountsPage() {
  const navigate = useNavigate()
  const picked = usePicked(FIRST_PICKED)
  const [uses, setUses] = useState(FIRST_USES)  // { sh1: '생활비', kb1: '고정지출' }
  const [kinds, setKinds] = useState({})         // 미분류 계좌에 사용자가 고른 종류 { kb3: '입출금' }
  const [error, setError] = useState('')
  const kindOf = (a) => a.kind ?? kinds[a.id] ?? null

  const next = () => {
    const unknown = ALL.find((a) => picked.has(a.id) && !kindOf(a))
    if (unknown) { setError(`${unknown.nickname ?? unknown.product}의 종류를 골라 주세요.`); return }
    navigate(PATHS.connectCards)
  }

  const purposeButtons = (a) => (
    <div className="mc-acctuse">
      <span>용도</span>
      {USES.map((u) => (
        <button key={u} type="button" aria-pressed={uses[a.id] === u} onClick={() => setUses({ ...uses, [a.id]: u })}>{u}</button>
      ))}
    </div>
  )

  const detail = (a) => {
    const kind = kindOf(a)
    if (!a.kind) {
      // 종류를 모르는 계좌: 먼저 종류를 고르고, 입출금이면 용도까지
      return (
        <>
          <div className="mc-acctuse mc-acctkind">
            <span>종류</span>
            {KIND_PICK.map(([v, label]) => (
              <button key={v} type="button" aria-pressed={kind === v}
                onClick={() => { setKinds({ ...kinds, [a.id]: v }); setError('') }}>{label}</button>
            ))}
          </div>
          {!kind && <p className="mc-acctpick-note">은행에서 계좌 종류를 알려 주지 않았어요. 어떤 통장인지 골라 주세요.</p>}
          {kind === '입출금' && purposeButtons(a)}
          {kind === '적금' && <p className="mc-acctpick-note">{SAVING_NOTE}</p>}
        </>
      )
    }
    if (a.use) return purposeButtons(a)
    return <p className="mc-acctpick-note">{a.note ?? SAVING_NOTE}</p>
  }

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" onClick={next}>선택한 계좌 {picked.list.length}개로 계속</button>}
    >
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>사용할 계좌를 골라 주세요</h1>
      <p className="mc-muted">연결한 은행에서 조회한 내 계좌예요. 예산에 쓸 계좌만 고르세요.</p>
      {BANKS.map((b) => (
        <div className="mc-card" key={b.bank}>
          <div className="mc-row"><h2>{b.bank}</h2><span className="mc-badge">연결 완료</span></div>
          {b.accounts.map((a) => {
            const kind = kindOf(a)
            return (
              <AcctPick key={a.id} on={picked.has(a.id)} onToggle={() => { picked.toggle(a.id); setError('') }}
                name={a.nickname ?? a.product}
                sub={`****${a.no} · ${kind ?? '종류 확인 필요'}${a.nickname ? ` · ${a.product}` : ''}`}
                amount={won(a.balance).replace('-', '−')} negative={a.balance < 0}>
                {detail(a)}
              </AcctPick>
            )
          })}
        </div>
      ))}
      <p className="mc-note">조회 가능한 계좌만 보여요. 별칭은 은행 앱에서 붙인 이름이에요. 같은 은행 계좌를 나중에 추가해도 다시 인증하지 않아요.</p>
      {error && <div className="mc-error" role="alert">{error}</div>}
    </Layout>
  )
}
