// 계좌 연결 1: 은행·카드사 고르기
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'

const LOAN_ANSWERS = ['있어요', '없어요', '나중에 확인']
const BANKS = ['신한', '국민', '우리', '하나', '농협']
const BANKS_SOON = ['카카오뱅크', '토스뱅크']  // CODEF 지원 확인 중
const CARDS = ['신한카드', '국민카드', '삼성카드', '현대카드', '롯데카드', '우리카드', '하나카드']

// 칩 여러 개 + "전체 선택" + "2곳 선택 · 신한, 국민" 을 한 덩어리로
function PickCard({ title, hint, items, picked, soon = [] }) {
  return (
    <div className="mc-card">
      <div className="mc-row">
        <h2>{title} <span className="mc-hint">{hint}</span></h2>
        <button type="button" className="mc-link" onClick={() => picked.set(items)}>전체 선택</button>
      </div>
      <div className="mc-chipbar">
        {items.map((x) => <button key={x} type="button" aria-pressed={picked.has(x)} onClick={() => picked.toggle(x)}>{x}</button>)}
        {soon.map((x) => <button key={x} type="button" className="mc-soonchip" disabled>{x} <small>지원 확인 중</small></button>)}
      </div>
      {/* 고른 순서가 아니라 화면 순서대로 이름을 보여 준다 */}
      <p className="mc-picked"><b>{picked.list.length}곳</b> 선택 · {items.filter(picked.has).join(', ')}</p>
    </div>
  )
}

export default function ConnectPage() {
  const navigate = useNavigate()
  const [hasLoan, setHasLoan] = useState(null)
  const banks = usePicked(['신한', '국민'])
  const cards = usePicked(['신한카드'])

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.connectAuth)}>선택한 기관 연결하기</button>}
    >
      <StepBar step={2} />
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>연결할 은행을 골라 주세요</h1>
      <p className="mc-muted">기관 인증 후 내 계좌 목록을 불러와요. 사용할 계좌는 다음 단계에서 골라요.</p>

      <div className="mc-card mc-loanq">
        <h2>상환 중인 대출이 있나요?</h2>
        <div className="mc-seg mc-seg-sm mc-loanq-seg" style={{ marginTop: 8 }}>
          {LOAN_ANSWERS.map((x) => <button key={x} type="button" aria-pressed={hasLoan === x} onClick={() => setHasLoan(x)}>{x}</button>)}
        </div>
        <p className="mc-picked">있으면 대출 정보도 불러와요.</p>
      </div>

      <PickCard title="연결할 은행" hint="필수 · 복수 선택" items={BANKS} picked={banks} soon={BANKS_SOON} />
      <PickCard title="카드사" hint="선택 · 나중에 해도 돼요" items={CARDS} picked={cards} />

      <label className="mc-field mc-check"><input id="mc-consent" type="checkbox" />[필수] 계좌·카드 조회 동의 (조회 전용)</label>
      <p className="mc-note">시안에서는 실제 인증 정보나 계좌 정보를 받지 않아요.</p>
      <div className="mc-error" role="alert"></div>
    </Layout>
  )
}
