// 팝업: 동의·데이터 관리 — 마이 "동의·데이터 관리"
import Popup from './Popup.jsx'

export default function PrivacyPopup() {
  return (
    <Popup>
      <h2>동의·데이터 관리</h2>
      <div className="mc-priv-row">
        <div><b>계좌 조회 · 필수</b><small>CODEF · 조회 전용 · 2026.10.01 동의 · v1</small></div>
        <span className="mc-badge">동의함</span>
      </div>
      <div className="mc-priv-row">
        <div><b>카드 조회 · 선택</b><small>2026.10.01 동의 · v1</small></div>
        {/* 철회·탈퇴는 기능 연결 전 */}
        <button type="button" className="mc-link">철회</button>
      </div>
      <div className="mc-priv-row">
        <div><b>대출 조회 · 선택</b><small>동의 안 함</small></div>
        <span className="mc-muted">-</span>
      </div>
      <p className="mc-note">선택 동의를 철회해도 계좌 기반 예산 기능은 그대로 써요. 필수 계좌 동의를 철회하면 새 계산·적용이 멈추고 다시 연결해야 해요.</p>
      <button type="button" className="mc-link mc-unlink">회원 탈퇴 · 데이터 삭제</button>
      <p className="mc-note">시안에서는 실제로 철회·삭제하지 않아요.</p>
    </Popup>
  )
}
