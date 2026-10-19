import Layout from '../components/Layout.jsx'
import Placeholder from '../components/Placeholder.jsx'

// 마이 (장고: users/my.html). 탭 화면이 아니라 하단 탭 없음
export default function MyPage() {
  return (
    <Layout title="마이" showBack>
      <Placeholder name="마이" />
    </Layout>
  )
}
