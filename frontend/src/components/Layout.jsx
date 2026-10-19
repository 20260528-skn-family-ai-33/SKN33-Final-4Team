// 공통 틀 (장고 templates/base.html)
// props: title = 화면 제목, showBack = 뒤로 가기 표시
//        activeTab = 켜진 탭 (탭 화면만. 있으면 하단 탭 + 오른쪽 위 마이 아이콘이 보인다)
//        foot = 화면 맨 아래 고정 버튼 (장고 {% block foot %})
//        children = 화면 본문
import Header from './Header.jsx'
import TabBar from './TabBar.jsx'
import { usePopup } from '../popups/context.js'

export default function Layout({ title, showBack = false, activeTab, foot, children }) {
  const { popup } = usePopup()
  const locked = !!popup  // 팝업이 떠 있으면 뒤 화면은 못 누르게 잠근다 (inert)

  return (
    // 디자인 CSS 가 전부 #mc-new-preview 아래에 걸려 있어서 id 를 그대로 쓴다
    <div id="mc-new-preview">
      <div className="mc-preview-stage">
        <div className="mc-device">
          <Header title={title} showBack={showBack} showMy={!!activeTab} inert={locked} />
          <div id="mc-toast" role="status"></div>
          <main id="mc-main" inert={locked}>
            <div className="mc-screen">
              <div className="mc-body">{children}</div>
              {foot && <div className="mc-foot">{foot}</div>}
            </div>
          </main>
          {/* 팝업 자리: 열린 팝업이 있으면 여기에 그려진다 */}
          <div id="mc-layer">{popup}</div>
          {activeTab && <TabBar active={activeTab} inert={locked} />}
        </div>
      </div>
    </div>
  )
}
