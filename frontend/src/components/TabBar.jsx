// 하단 탭 (장고 templates/_tabbar.html)
// <Link> = 새로고침 없이 화면을 바꾸는 링크. 장고 <a href> 와 같은 역할
import { Link } from 'react-router'
import { Receipt, Wallet, House, Shield, MessageCircle } from 'lucide-react'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import MenuPopup from '../popups/MenuPopup.jsx'

const TABS = [
  { id: 'txs', label: '거래', Icon: Receipt },
  { id: 'budget', label: '예산', Icon: Wallet },
  { id: 'home' },  // 가운데 홈 버튼 자리
  { id: 'fund', label: '쿠션', Icon: Shield },
  { id: 'assistant', label: '챗봇', Icon: MessageCircle },
]

export default function TabBar({ active, inert = false }) {
  const { openPopup } = usePopup()

  return (
    <nav id="mc-nav" aria-label="주 메뉴" inert={inert}>
      {TABS.map(({ id, label, Icon }) =>
        id === 'home' ? (
          <div className="mc-center" key="home">
            <Link className="mc-home" to={PATHS.home} aria-label="홈">
              <House size={20} aria-hidden="true" />
            </Link>
            <button type="button" className="mc-expand" onClick={() => openPopup(<MenuPopup />)}>돈관리 ∧</button>
          </div>
        ) : (
          <Link key={id} to={PATHS[id]} aria-current={active === id ? 'page' : undefined}>
            <Icon size={21} aria-hidden="true" />
            <span>{label}</span>
          </Link>
        )
      )}
    </nav>
  )
}
