import { Link } from 'react-router'
import Layout from '../components/Layout.jsx'

// 없는 주소로 들어왔을 때
export default function NotFoundPage() {
  return (
    <Layout title="페이지 없음" showBack>
      <section className="mc-card">
        <h2>없는 화면이에요</h2>
        <Link className="mc-link" to="/">홈으로 가기</Link>
      </section>
    </Layout>
  )
}
