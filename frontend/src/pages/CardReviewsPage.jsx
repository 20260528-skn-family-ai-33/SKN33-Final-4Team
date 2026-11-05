import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import CardReviewList from '../components/CardReviewList.jsx'
import { PATHS } from '../routes.js'

export default function CardReviewsPage() {
  const navigate = useNavigate()
  const { state } = useLocation()
  return <Layout title="결제 카드 확인" showBack>
    <h1>결제 카드를 확인해 주세요</h1>
    <p className="mc-muted">카드 내역은 있지만 어떤 카드로 결제했는지 확인이 필요한 거래예요.</p>
    <CardReviewList />
    <button className="mc-primary" type="button" onClick={() => navigate(PATHS.protect, { state: state?.protectState })}>지킬 돈 설정으로 돌아가기</button>
    <button className="mc-link" type="button" onClick={() => navigate(PATHS.connections)}>연결 관리로 가기</button>
  </Layout>
}
