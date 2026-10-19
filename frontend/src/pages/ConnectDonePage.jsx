// 계좌 연결 5: 연결 결과 (장고: finance/templates/finance/connect_done.html)
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import InstRow from '../components/InstRow.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const ACCOUNTS = [
  { name: '신한 생활비통장', sub: '****1234 · 입출금 · 생활비', balance: 340_000 },
  { name: '국민 급여통장', sub: '****5678 · 입출금 · 고정지출', balance: 2_460_000 },
]

export default function ConnectDonePage() {
  const navigate = useNavigate()

  return (
    <Layout title="계좌 연결" showBack foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.loanCheck)}>다음</button>}>
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>연결을 마쳤어요</h1>
      <p className="mc-muted">은행 2곳 · 계좌 2개 · 카드사 1곳 · 카드 2장을 연결했어요.</p>
      <div className="mc-card">
        {ACCOUNTS.map((a) => <InstRow key={a.name} name={a.name} sub={a.sub} right={<strong>{won(a.balance)}</strong>} />)}
      </div>
      <div className="mc-card">
        <div className="mc-keyvalue"><span>잔액 조회</span><strong className="ok-t"><Icon name="CircleCheck" /> 완료 · 오늘 09:30</strong></div>
        <div className="mc-keyvalue"><span>거래 내역</span><strong className="warn-t"><Icon name="Loader" /> 최근 3개월 수집 중</strong></div>
        <div className="mc-keyvalue"><span>카드 청구서</span><strong>조회됨</strong></div>
      </div>
    </Layout>
  )
}
