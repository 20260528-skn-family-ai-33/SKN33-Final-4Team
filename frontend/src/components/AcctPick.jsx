// 계좌·카드 고르기 카드 (누르면 ✓ 켜고 끄기)
// props: on = 골랐는지, onToggle = 눌렀을 때, name/sub = 이름·작은 글씨, amount = 오른쪽 금액(없어도 됨)
//        children = 아래에 붙는 것 (용도 버튼, 안내 문구 등) / dim = 흐리게 (휴면 카드 등)
export default function AcctPick({ on, onToggle, name, sub, amount, negative = false, dim = false, children }) {
  return (
    <div className={['mc-acctpick', on && 'on', dim && 'is-dim'].filter(Boolean).join(' ')}>
      <button type="button" className="mc-acctpick-row" aria-pressed={on} onClick={onToggle}>
        <span className="mc-acctpick-box">{on ? '✓' : ''}</span>
        <span className="mc-grow"><strong>{name}</strong><small>{sub}</small></span>
        {amount !== undefined && <strong className={negative ? 'mc-neg' : ''}>{amount}</strong>}
      </button>
      {children}
    </div>
  )
}
