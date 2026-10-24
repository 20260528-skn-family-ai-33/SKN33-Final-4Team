// 팝업: 거래 상세 — 거래 목록에서 거래 한 줄 누르기
// props: tx = 누른 거래, date = 그 거래의 날짜, categories = 카테고리 목록
// 거래 종류별 (docs/api/transactions.md, 10/22 결정 V① · W · 10/24 AA⑤~⑦)
//   소비(normal)      카테고리 수정. 고르면 이후 같은 가맹점 분류에 반영
//   월급 입금(income)  카테고리를 고를 수 없음 (보내면 400, AA⑦). 소비 합계 · 예산에 안 들어감
//   이체(transfer)     처음엔 소비 아님. [소비로 분류하기]로 카테고리를 고르면 그때부터 소비 (W)
//   원래 이체였던 소비  [이체로 되돌리기] = PATCH { category: null } (W · AA⑥). 받는 사람은 기억하지 않음
//   고정지출 연결(funding: fixed)  '고정' — 생활비 사용액에서 빠짐. 월세 이체도 고정지출에 연결되면 여기에 해당 (AA⑤)
// 저장은 API 연결 후: apiSend(`/api/finance/transactions/${id}/`, 'PATCH', { category })
import { useState } from 'react'
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { won } from '../utils/money.js'

export default function TransactionPopup({ tx, date, categories }) {
  const { closePopup } = usePopup()
  // 이체 화면에서만 쓰는 단계: 'idle' → 'pick'(카테고리 고르는 중)
  const [step, setStep] = useState('idle')
  const isIncome = tx.kind === 'income'
  const isTransfer = tx.kind === 'transfer'
  const wasTransfer = tx.original === 'transfer'
  const isFixed = tx.funding === 'fixed'

  return (
    <Popup label="거래 상세" sub>
      <div className="mc-eyebrow">{isIncome ? '월급 입금' : isTransfer ? '보낸 이체' : '거래 상세'}</div>
      <h1>{tx.name}</h1>
      <div className="mc-card">
        <div className="mc-money">
          {isIncome ? <span className="mc-tx-in">+{won(tx.amount)}</span>
            : tx.cancel === 'full' ? <s>{won(tx.amount)}</s> : won(tx.net ?? tx.amount)}
        </div>
        {tx.cancel && (
          <p className="mc-cancel-box" role="note">
            {tx.cancel === 'full'
              ? '카드사에서 취소된 거래예요. 소비에서 빠졌어요.'
              : `${won(tx.amount)} 중 ${won(tx.cancelAmount)}이 취소됐어요. 소비에는 ${won(tx.net)}만 들어가요.`}
          </p>
        )}
        <div className="mc-keyvalue"><span>거래일</span><strong>{date}</strong></div>
        <div className="mc-keyvalue"><span>{isIncome ? '들어온 계좌' : isTransfer || wasTransfer ? '보낸 계좌' : '결제 수단'}</span><strong>{tx.via ?? '신한 · 1234'}</strong></div>
        {!isIncome && !isTransfer && <div className="mc-keyvalue"><span>카테고리</span><strong>{tx.cat}</strong></div>}
        {!isIncome && !isTransfer && (
          <div className="mc-keyvalue"><span>분류</span><strong>{wasTransfer ? '내가 소비로 분류함' : 'AI 자동 분류'}</strong></div>
        )}
        {isFixed && <div className="mc-keyvalue"><span>고정지출</span><strong>{tx.fixedName} <em className="mc-tx-tag is-fixed">고정</em></strong></div>}
      </div>

      {/* 월급: 카테고리를 고를 수 없다 */}
      {isIncome && (
        <div className="mc-card">
          <p className="mc-muted" style={{ margin: 0 }}>월급으로 찾은 입금이에요. 거래 목록에는 보이지만 소비 합계 · 예산에는 들어가지 않아요. 입금은 카테고리를 고를 수 없어요.</p>
        </div>
      )}

      {/* 이체: 처음엔 소비가 아님 → [소비로 분류하기] */}
      {isTransfer && step === 'idle' && (
        <div className="mc-card">
          <h2>남에게 보낸 이체예요</h2>
          <p className="mc-muted" style={{ marginTop: 4 }}>처음에는 소비로 세지 않아요. 친구에게 밥값을 보냈다면 카테고리를 골라 소비로 넣어 주세요.</p>
          <button type="button" className="mc-primary" onClick={() => setStep('pick')}>소비로 분류하기</button>
        </div>
      )}
      {isTransfer && step === 'pick' && (
        <div className="mc-card">
          <h2>어디에 쓴 돈인가요?</h2>
          {/* 고르면 PATCH { category } → transactionType: spending, 소비 합계 · 예산에 들어감 */}
          <div className="mc-pend-pick">
            {categories.map((cat) => <button type="button" key={cat} onClick={closePopup}>{cat}</button>)}
          </div>
          <p className="mc-note" style={{ paddingBottom: 0 }}>이 거래만 바뀌어요. 같은 사람에게 다음에 보내는 이체는 다시 이체로 들어와요. 나중에 이체로 되돌릴 수 있어요.</p>
        </div>
      )}

      {/* 소비: 카테고리 수정 */}
      {!isIncome && !isTransfer && (
        <div className="mc-card">
          <h2>카테고리 수정</h2>
          {/* 고르면 닫힌다 · 저장은 DB 연결 후 */}
          <div className="mc-pend-pick">
            {categories.map((cat) => (
              <button type="button" key={cat} onClick={closePopup}>{cat === tx.cat && '✓ '}{cat}</button>
            ))}
          </div>
          <p className="mc-note" style={{ paddingBottom: 0 }}>
            {wasTransfer ? '이 거래만 바뀌어요. 받는 사람은 기억하지 않아요.' : '고른 카테고리는 이후 같은 가맹점 분류에 반영돼요.'}
            {isFixed && ' 고정지출 연결은 그대로예요.'}
          </p>
        </div>
      )}

      {/* 원래 이체였던 소비 → 되돌리기 (PATCH { category: null }). 고정지출에 연결된 이체는 연결을 먼저 풀어야 함 */}
      {wasTransfer && !isFixed && (
        <button type="button" className="mc-secondary" onClick={closePopup}>이체로 되돌리기</button>
      )}
      {wasTransfer && isFixed && (
        <p className="mc-note">고정지출에 연결된 이체예요. 이체로 되돌리려면 고정지출 화면에서 연결을 먼저 풀어 주세요.</p>
      )}
    </Popup>
  )
}
