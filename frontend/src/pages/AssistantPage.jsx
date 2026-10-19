// 통합 챗봇 (장고: chat/templates/chat/assistant.html). 지금은 첫 화면(선택지 3개)만
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'

export default function AssistantPage() {
  return (
    <Layout title="통합 챗봇" activeTab="assistant">
      <div className="mc-bc mc-asst">
        <div className="mc-bc-ctx mc-asst-ctx">
          <span className="mc-cat-ic mc-big-ic"><Icon name="Sparkles" /></span>
          <div>
            <em className="mc-asst-who">라우터</em>
            <strong>머니쿠션 챗봇</strong>
            <small>하고 싶은 일을 고르면 맞는 도우미를 연결해 드려요</small>
          </div>
        </div>
        <div className="mc-bc-log mc-chat-log mc-asst-log" aria-live="polite">
          <div className="mc-msg bot"><div className="mc-bubble">안녕하세요! 무엇을 하고 싶으신가요?</div></div>
          {/* 선택지를 누른 뒤 대화는 챗봇 기능을 만들 때 붙인다 */}
          <div className="mc-asst-opts" role="group" aria-label="하고 싶은 일 선택">
            <button type="button" className="mc-asst-opt">
              <span className="mc-asst-ic"><Icon name="ChartNoAxesCombined" /></span>
              <span className="mc-grow">소비를 분석해주세요</span>
              <Icon name="ChevronRight" />
            </button>
            <button type="button" className="mc-asst-opt">
              <span className="mc-asst-ic"><Icon name="SlidersHorizontal" /></span>
              <span className="mc-grow">예산을 조정하고 싶어요</span>
              <Icon name="ChevronRight" />
            </button>
            <button type="button" className="mc-asst-opt is-soon" disabled aria-disabled="true">
              <span className="mc-asst-ic"><Icon name="Landmark" /></span>
              <span className="mc-grow">큰 지출을 상담하고 싶어요</span>
              <span className="mc-soon">준비 중</span>
            </button>
          </div>
        </div>
      </div>
    </Layout>
  )
}
