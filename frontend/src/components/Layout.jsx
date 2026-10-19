// 공통 틀 (장고 templates/base.html)
// props: title = 화면 제목, showBack = 뒤로 가기 표시, activeTab = 켜진 탭(없으면 하단 탭 숨김)
//        children = 화면 본문
import Header from './Header.jsx'
import TabBar from './TabBar.jsx'

export default function Layout({ title, showBack = false, activeTab, children }) {
  return (
    // 디자인 CSS 가 전부 #mc-new-preview 아래에 걸려 있어서 id 를 그대로 쓴다
    <div id="mc-new-preview">
      <div className="mc-preview-stage">
        <div className="mc-device">
          <Header title={title} showBack={showBack} />
          <div id="mc-toast" role="status"></div>
          <main id="mc-main">
            <div className="mc-screen">
              <div className="mc-body">{children}</div>
            </div>
          </main>
          {/* 팝업 자리 (5단계) */}
          <div id="mc-layer"></div>
          {activeTab && <TabBar active={activeTab} />}
        </div>
      </div>
    </div>
  )
}
