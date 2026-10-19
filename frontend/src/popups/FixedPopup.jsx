// 팝업: 고정지출 추가 / 수정
// props: item = 고칠 고정지출 (없으면 "추가", 있으면 "수정")
// 저장·삭제는 아직 안 되고 닫히기만 한다
import Popup from './Popup.jsx'
import { usePopup } from './context.js'
import { MONTH_DAYS } from '../utils/days.js'

const ACCOUNTS = ['신한 · 1234', '국민 · 5678']
const FIXED_CATS = ['식비', '주거/통신', '교통/차량', '생활용품', '건강/미용', '여가/관계', '저축/투자/부채']

export default function FixedPopup({ item }) {
  const { closePopup } = usePopup()
  const editing = !!item

  const onSubmit = (e) => {
    e.preventDefault()  // 페이지 새로고침 막기
    closePopup()        // 저장은 API 연결 후
  }

  return (
    <Popup label="고정지출 수정" sub>
      <div className="mc-eyebrow">{editing ? '고정지출 수정' : '고정지출 추가'}</div>
      <h1>{editing ? item.name : '매달 나가는 돈 추가'}</h1>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit}>
        {/* defaultValue = 처음 채워 둘 값 (수정일 때만) */}
        <label className="mc-field">항목 이름<input name="fxName" placeholder="월세, 통신비 등" type="text" defaultValue={item?.name} /></label>
        <label className="mc-field">금액<input inputMode="numeric" min="0" name="fxAmount" placeholder="예: 55000" step="1" type="number" defaultValue={item?.amount} /></label>
        <label className="mc-field">출금일
          <select defaultValue={item ? `매월 ${item.day}일` : undefined}>{MONTH_DAYS.map((d) => <option key={d}>매월 {d}일</option>)}</select>
        </label>
        <label className="mc-field">출금 계좌
          <select defaultValue={item?.account}>{ACCOUNTS.map((a) => <option key={a}>{a}</option>)}</select>
        </label>
        <label className="mc-field">카테고리
          <select defaultValue={item?.cat}>{FIXED_CATS.map((c) => <option key={c}>{c}</option>)}</select>
        </label>
        <div className="mc-error" role="alert"></div>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit">{editing ? '수정 저장' : '추가하기'}</button></div>
      </form>
      {editing && <button type="button" className="mc-secondary">이 고정지출 삭제</button>}
    </Popup>
  )
}
