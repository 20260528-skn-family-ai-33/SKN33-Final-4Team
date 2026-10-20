// 계좌 연결 5: 연결 결과
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import InstRow from '../components/InstRow.jsx'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const ACCOUNTS = [
  { name: '신한 저축예금', sub: '****1234 · 입출금 · 생활비', balance: 340_000 },
  { name: '국민 급여통장', sub: '****5678 · 입출금 · 고정지출', balance: 2_460_000 },
]
// 기관이 실제로 조회해 준 기간 (CODEF 응답 commStartDate ~ commEndDate). 기관마다 가능 기간이 달라 요청보다 짧을 수 있다
// 시안 예시: 3개월(07.20 ~ 10.20)을 요청
const PERIODS = [
  { inst: '신한은행', from: '07.20', to: '10.20' },
  { inst: '국민은행', from: '07.20', to: '10.20' },
  { inst: '신한카드', from: '07.20', to: '10.20' },
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
      <div className="mc-card mc-periods">
        <h2>불러온 거래 기간</h2>
        {PERIODS.map((p) => <div className="mc-keyvalue" key={p.inst}><span>{p.inst}</span><strong>{p.from}부터 불러왔어요</strong></div>)}
        <p className="mc-note" style={{ paddingBottom: 0 }}>기관마다 불러올 수 있는 기간이 달라요. 요청한 기간보다 짧으면 그 기관은 짧게 보여요.</p>
      </div>
    </Layout>
  )
}
