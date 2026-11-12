// 팝업(가운데 작은 창): 홈 새로고침에서 불러오지 못한 기관 (11/12)
// 성공한 기관은 토스트로 잠깐 보여 주고, 실패는 사용자가 할 일이 있어서 이 창으로 알린다 (예전에는 홈 위에 줄로 남았음)
// props: lines = 실패한 기관 [{ name, text }] (financeSync.syncLines), error = 불러오기 자체가 안 됐을 때 문장, onManage = 연결 관리로
import Popup from './Popup.jsx'
import { usePopup } from './context.js'

export default function SyncResultPopup({ lines = [], error = '', onManage }) {
  const { closePopup } = usePopup()
  return (
    <Popup label="불러오기 결과" dialog>
      <h2>{error ? '불러오지 못했어요' : '불러오지 못한 곳이 있어요'}</h2>
      <div role="alert" style={{ wordBreak: 'keep-all' }}>
        {error && <p className="mc-muted" style={{ margin: '6px 0 0' }}>{error}</p>}
        {lines.map((l) => <p key={l.name} className="mc-muted" style={{ margin: '6px 0 0' }}><strong>{l.name}</strong> · {l.text}</p>)}
      </div>
      {lines.length > 0 && onManage && (
        <button type="button" className="mc-primary" onClick={() => { closePopup(); onManage() }}>연결 관리에서 확인하기</button>
      )}
    </Popup>
  )
}
