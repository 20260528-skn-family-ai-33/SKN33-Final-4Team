// 계좌 연결 2: 기관 인증 결과
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import InstRow from '../components/InstRow.jsx'
import { PATHS } from '../routes.js'

const BANKS = ['신한은행', '국민은행']
const CARDS = ['신한카드']
const ok = <Icon name="CircleCheck" />

export default function ConnectAuthPage() {
  const navigate = useNavigate()

  return (
    <Layout
      title="계좌 연결"
      showBack
      foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.connectAccounts)}>보유 계좌 불러오기 · 은행 {BANKS.length}곳</button>}
    >
      <div className="mc-eyebrow">계좌 연결</div>
      <h1>금융기관 연결 상태예요</h1>
      <p className="mc-muted">기관마다 인증을 마친 뒤 보유 계좌를 불러와요.</p>
      <div className="mc-card">
        <h2>은행</h2>
        {BANKS.map((b) => <InstRow key={b} name={b} sub="연결 완료" subClass="ok" right={ok} />)}
      </div>
      <div className="mc-card">
        <h2>카드사</h2>
        {CARDS.map((c) => <InstRow key={c} icon="CreditCard" name={c} sub="연결 완료" subClass="ok" right={ok} />)}
      </div>
      <p className="mc-note">연결에 실패한 기관이 있어도 성공한 기관의 계좌는 그대로 쓸 수 있어요.</p>
      <div className="mc-error" role="alert"></div>
    </Layout>
  )
}
