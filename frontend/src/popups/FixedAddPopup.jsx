// 팝업: 고정지출 추가 (장고 budgets/_fixed_add.html) — 저장은 아직 안 되고 닫히기만 한다
import Popup from './Popup.jsx'
import { usePopup } from './context.js'

const DAYS = Array.from({ length: 31 }, (_, i) => `매월 ${i + 1}일`)  // 매월 1일 ~ 31일
const ACCOUNTS = ['신한 · 1234', '국민 · 5678']
const FIXED_CATS = ['식비', '주거/통신', '교통/차량', '생활용품', '건강/미용', '여가/관계', '저축/투자/부채']

export default function FixedAddPopup() {
  const { closePopup } = usePopup()

  const onSubmit = (e) => {
    e.preventDefault()  // 페이지 새로고침 막기
    closePopup()        // 저장은 DB 연결 후 (6~7단계)
  }

  return (
    <Popup label="고정지출 수정" sub>
      <div className="mc-eyebrow">고정지출 추가</div>
      <h1>매달 나가는 돈 추가</h1>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        <label className="mc-field">항목 이름<input name="fxName" placeholder="월세, 통신비 등" type="text" /></label>
        <label className="mc-field">금액<input inputMode="numeric" min="0" name="fxAmount" placeholder="예: 55000" step="1" type="number" /></label>
        <label className="mc-field">출금일<select>{DAYS.map((d) => <option key={d}>{d}</option>)}</select></label>
        <label className="mc-field">출금 계좌<select>{ACCOUNTS.map((a) => <option key={a}>{a}</option>)}</select></label>
        <label className="mc-field">카테고리<select>{FIXED_CATS.map((c) => <option key={c}>{c}</option>)}</select></label>
        <div className="mc-error" role="alert"></div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit">추가하기</button></div>
      </form>
    </Popup>
  )
}
