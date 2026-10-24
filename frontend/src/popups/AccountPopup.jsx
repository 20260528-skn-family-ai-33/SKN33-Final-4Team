// 팝업: 연결 상세 · 은행 계좌
// props: acct = 연결 관리 화면에서 누른 계좌
import Popup from './Popup.jsx'
import RefreshRow from '../components/RefreshRow.jsx'
import Icon from '../components/Icon.jsx'
import { won } from '../utils/money.js'
import { usePopup } from './context.js'
import DisconnectPopup from './DisconnectPopup.jsx'

export default function AccountPopup({ acct }) {
  const { openPopup } = usePopup()
  // 이 은행 계좌로 나가는 고정지출 · 정기 저축 → 해제 확인 창에서 같이 그만할지 묻는다 (결정 F)
  const related = [
    ...acct.fixed.map((f) => ({ id: f.name, type: 'fixed', name: f.name, line: `매월 ${f.day}일 · ${acct.short} ****${acct.no}`, amount: f.amount })),
    ...(acct.savings ?? []).map((v) => ({ id: v.name, type: 'saving', name: v.name, line: `매월 ${v.day}일 · ${acct.short} ****${acct.no}에서 보냄`, amount: v.amount })),
  ]
  return (
    <Popup label="연결 상세" sub>
      <div className="mc-eyebrow">{acct.bank} · {acct.kind}</div>
      <h1>{acct.nickname ?? acct.name}</h1>
      <div className="mc-card">
        <div className="mc-keyvalue"><span>잔액</span><strong>{won(acct.balance)}</strong></div>
        <div className="mc-keyvalue"><span>계좌번호</span><strong>****{acct.no}</strong></div>
        <div className="mc-keyvalue"><span>용도</span><strong>{acct.use}</strong></div>
        <div className="mc-keyvalue"><span>마지막 조회</span><strong>{acct.updated} · 거래 수집 중</strong></div>
        <div className="mc-keyvalue"><span>상품명</span><strong>{acct.name}</strong></div>
        <div className="mc-keyvalue mc-alias-kv"><span>별칭</span><strong>{acct.nickname ?? <em>없음</em>}<button type="button" className="mc-alias-btn">수정</button></strong></div>
      </div>
      <div className="mc-card">
        <h2>이 은행에서 나가는 고정지출</h2>
        {acct.fixed.map((f) => <div className="mc-keyvalue" key={f.name}><span>{f.name}</span><strong>매월 {f.day}일 · {won(f.amount)}</strong></div>)}
      </div>
      {/* 새로고침 · 선택 해제는 기능 연결 전. 연결 해제는 확인 창까지 (결정 F) */}
      <div className="mc-card mc-acct-act">
        <RefreshRow target={acct.bank} />
        <button type="button" className="mc-listrow"><Icon name="CircleMinus" /><span>이 계좌 선택 해제</span><small className="mc-muted">머니쿠션 관리 대상에서만 빠져요</small></button>
        <button type="button" className="mc-listrow mc-unlink" onClick={() => openPopup(<DisconnectPopup inst={acct.bank} kind="bank" related={related} />)}><Icon name="Unlink" /><span>{acct.bank} 연결 전체 해제</span></button>
      </div>
    </Popup>
  )
}
