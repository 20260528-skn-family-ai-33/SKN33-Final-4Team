// 거래 내역 — GET /api/finance/transactions/ (docs/api/transactions.md '거래 목록 보기')
// 주소 ?category=FOOD,CULTURE_LEISURE 처럼 카테고리 코드로 골라 본다 (여러 개). 고르는 일 · 합계는 장고 API 가 해서 보내 준다
// 거래 유형 · 카테고리 · 계좌 · 카드는 모두 [필터] 팝업에서 고른다 (11/9: 위에 늘 떠 있던 카테고리 칩 줄이 두꺼워서 팝업으로)
//   → 주소 ?type=…&category=…&account=…&card=… (API 의 transactionType · accountId · cardId, 계좌 · 카드를 둘 다 고르면 둘 중 하나)
// '전체'에는 소비 + 입금(월급은 isSalary) + 남에게 보낸 이체가 보인다 (10/22 결정 V①). 입금 · 이체는 카테고리가 없고 합계에 안 들어간다
//   이체를 누르면 [소비로 분류하기] (결정 W) · 고정지출로 연결된 거래(funding: fixed)는 '고정' 표시 (★8 · AA⑤)
// 기간은 달력월. ‹ › 로 달을 옮긴다 (주소 ?month=2026-10, 없으면 이번 달)
// '확인 필요'는 기본이 전체 기간(H-03 — 홈 숫자와 같게), [달별로 보기]로 달마다도 본다. 칩 · 배너 숫자는 늘 전체 기간
// 날짜별 묶음 · 날짜 합계는 화면에서 만든다 (API 는 평평한 목록)
// 이름 검색(11/11): 입력하는 대로 바로 거른다. 보고 있는 달 · 필터로 이미 받은 목록 안에서 화면이 거르고, 소비 합계도 걸러진 거래로 다시 센다
//   (서버에 다시 묻지 않아서 글자마다 목록이 깜빡이지 않는다). 달 · 필터를 바꿔도 검색어는 남고, '확인 필요' 배너를 누르면 풀린다
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import { won } from '../utils/money.js'
import { useApi } from '../utils/api.js'
import { PENDING } from '../utils/categories.js'
import { CHIPS, toView } from '../utils/transactions.js'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import TransactionPopup from '../popups/TransactionPopup.jsx'
import TxPickPopup from '../popups/TxPickPopup.jsx'
import FilterPopup from '../popups/FilterPopup.jsx'

// 다른 화면(홈 · 예산 · 소비 분석 카드)이 아직 ?category=확인 필요 처럼 이름으로 보내서, 이름이 오면 코드로 바꿔 읽는다
const CODE_BY_NAME = Object.fromEntries([...CHIPS, PENDING].map((c) => [c.name, c.code]))
// '2026-10-11' → '10월 11일' (오늘이면 ' · 오늘')
function dayLabel(iso) {
  const [, m, d] = iso.split('-').map(Number)
  const t = new Date()
  const isToday = t.getMonth() + 1 === m && t.getDate() === d && String(t.getFullYear()) === iso.slice(0, 4)
  return `${m}월 ${d}일${isToday ? ' · 오늘' : ''}`
}

// 내 계좌 이체 판단(R11) → 목록 한 줄 문구. 거절(rejected)은 일반 이체로 보인다
const OWN_ROW = { suggested: '내 계좌 이체 추정', auto: '내 계좌 이체 · 자동 분류', confirmed: '내 계좌 이체' }

// 날짜별로 묶고, 날짜 합계는 소비만 (입금 · 이체 제외)
function groupByDay(items) {
  const days = []
  for (const item of items) {
    let day = days.at(-1)
    if (!day || day.date !== item.occurredOn) days.push((day = { date: item.occurredOn, items: [], total: 0 }))
    day.items.push(toView(item))
    if (item.transactionType === 'spending') day.total += item.netAmount
  }
  return days
}

// 'YYYY-MM' 달 계산 (Date 가 연도 넘김을 알아서 한다)
const ym = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
const shiftMonth = (month, n) => ym(new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1 + n, 1))
const lastDay = (month) => new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate()

// 그 달의 조회 기간. 이번 달은 to 를 안 보내서 서버가 오늘까지로 자른다
function monthQuery(month, thisMonth) {
  return `from=${month}-01${month === thisMonth ? '' : `&to=${month}-${lastDay(month)}`}`
}

// 화면 위 제목: '10월', 다른 해면 '2025년 12월'
function monthLabel(month, thisMonth) {
  const m = `${Number(month.slice(5, 7))}월`
  return month.slice(0, 4) === thisMonth.slice(0, 4) ? m : `${month.slice(0, 4)}년 ${m}`
}

export default function TransactionsPage() {
  // 주소의 ?category= 값(카테고리 코드, 쉼표로 여러 개)을 읽고 바꾸는 도구. 없으면 전체
  const [params, setParams] = useSearchParams()
  const list = (name) => (params.get(name) ?? '').split(',').filter(Boolean)
  const codes = list('category').map((c) => CODE_BY_NAME[c] ?? c)
  const accountIds = list('account')
  const cardIds = list('card')
  const types = list('type')  // spending · income · transfer
  const isPending = codes.length === 1 && codes[0] === PENDING.code  // 확인 필요는 다른 카테고리와 섞지 않는다
  const [thisMonth] = useState(() => ym(new Date()))  // 화면을 연 날 기준
  // ?month= 값: 'YYYY-MM' 또는 'all'(확인 필요만). 없으면 확인 필요는 전체 기간, 그 밖은 이번 달
  const month = params.get('month') ?? (isPending ? 'all' : thisMonth)
  const all = isPending && month === 'all'
  const shown = month === 'all' ? thisMonth : month
  // 기본값과 같은 달은 주소에 안 남긴다
  const go = (cats, m, acc = accountIds, crd = cardIds, typ = types) => setParams({
    ...(cats.length && { category: cats.join(',') }),
    ...(m !== (cats.length === 1 && cats[0] === PENDING.code ? 'all' : thisMonth) && { month: m }),
    ...(acc.length && { account: acc.join(',') }),
    ...(crd.length && { card: crd.join(',') }),
    ...(typ.length && { type: typ.join(',') }),
  })
  // 확인 필요는 그것만 · 전체 기간(숫자와 같게). 다른 카테고리로 바꾸면 보던 달, 그대로면 지금 달 그대로
  const applyFilter = (cats, acc, crd, typ) => {
    const toPending = cats[0] === PENDING.code
    go(cats, toPending === isPending ? month : toPending ? 'all' : shown, acc, crd, typ)
  }
  const { openPopup } = usePopup()
  // 카테고리 · 달을 바꾸면 주소가 바뀌고 → 장고에게 다시 받아온다
  const query = [
    codes.length && `category=${codes.map(encodeURIComponent).join(',')}`,
    accountIds.length && `accountId=${accountIds.join(',')}`,
    cardIds.length && `cardId=${cardIds.join(',')}`,
    types.length && `transactionType=${types.map(encodeURIComponent).join(',')}`,
    !all && monthQuery(shown, thisMonth),
  ].filter(Boolean).join('&')
  const filterCount = types.length + codes.length + accountIds.length + cardIds.length
  // 필터를 바꾸면 새 결과가 올 때까지 data = null — 이전 조건의 목록 · 합계를 새 조건 값처럼 보이지 않는다 (R29)
  // keepData: 카테고리 저장 뒤 다시 받기가 실패해도 목록을 남기고 아래에서 실패 안내를 띄운다
  const { data, error, reload, refreshing } = useApi(`/api/finance/transactions/?${query}`, { keepData: true })
  // '확인 필요' 건수 = 기간 없이 전체 (H-03). 칩 · 배너 숫자가 홈과 같아진다
  const { data: pending, reload: reloadPending } = useApi('/api/finance/transactions/?category=UNCLASSIFIED')
  // 카테고리를 저장하면 목록과 '확인 필요' 건수를 다시 받는다
  const refresh = () => { reload(); reloadPending() }
  const pendingCount = pending?.summary.needsReviewCount ?? 0

  // 이름 검색: 목록 이름(카드 = 가맹점명, 계좌 = 적요)에 그 글자가 있는 거래만. 영어는 대소문자 무시
  const [search, setSearch] = useState('')
  const needle = search.trim().toLowerCase()
  const items = !data ? [] : needle ? data.items.filter((item) => (item.merchantName ?? '').toLowerCase().includes(needle)) : data.items
  // 소비 합계: 검색 중이면 걸러진 소비 거래의 netAmount 합 (서버 spentTotal 과 같은 계산)
  const spentTotal = !data ? 0 : needle
    ? items.reduce((sum, item) => sum + (item.transactionType === 'spending' ? item.netAmount : 0), 0)
    : data.summary.spentTotal

  // 받는 동안에도 달 · 칩 · 필터는 그대로 두어 계속 바꿀 수 있게 한다
  const days = groupByDay(items)

  return (
    <Layout title="거래 내역" activeTab="txs">
      <div className="mc-sumbar">
        {all ? <strong className="mc-sumbar-m">전체 기간</strong> : <>
          <button type="button" className="mc-icon" aria-label="이전 달" onClick={() => go(codes, shiftMonth(shown, -1))}>‹</button>
          <strong className="mc-sumbar-m">{monthLabel(shown, thisMonth)}</strong>
          <button type="button" className="mc-icon" aria-label="다음 달" disabled={shown >= thisMonth} onClick={() => go(codes, shiftMonth(shown, 1))}>›</button>
        </>}
        {isPending && (
          <button type="button" className="mc-sumbar-toggle" onClick={() => go(codes, all ? thisMonth : 'all')}>{all ? '달별로 보기' : '전체 기간 보기'}</button>
        )}
        {/* 이 목록의 소비 소계일 뿐 — 예산 · 남은 생활비는 홈 · 예산 화면 값 (급여 주기 기준이라 다름) */}
        {data && <span className="mc-sumbar-v"><b>{won(spentTotal)}</b><span className="mc-muted"> 소비</span></span>}
      </div>

      {pendingCount > 0 && (
        // 배너 숫자는 다른 조건 없이 센 전체 건수 → 누르면 거래 유형 · 계좌 · 카드 조건을 풀고 그 건수 그대로 보여 준다
        //   (입금 · 이체만 골라 둔 채 누르면 '확인 필요(소비)'와 겹치는 거래가 없어 늘 비던 문제, PR #101 리뷰)
        <button type="button" className="mc-pend-banner" aria-pressed={isPending} onClick={() => { setSearch(''); go([PENDING.code], 'all', [], [], []) }}>
          <span className="mc-pend-dot">{pendingCount}</span>
          <strong className="mc-grow">카테고리 확인이 필요한 거래</strong>
          <span className="mc-muted">›</span>
        </button>
      )}

      {/* 입력하는 대로 바로 걸러진다. 돋보기 · 엔터는 키보드를 닫는다(이미 걸러진 상태) */}
      <form className="mc-tx-search" role="search" onSubmit={(e) => { e.preventDefault(); e.currentTarget.querySelector('input')?.blur() }}>
        <input type="search" name="q" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="이름으로 검색"
          aria-label="거래 이름 검색" enterKeyHint="search" maxLength={50} autoComplete="off" />
        <button type="submit" className="mc-tx-search-btn" aria-label="검색"><Icon name="Search" size={16} /></button>
      </form>

      <div className="mc-row mc-tx-sum">
        <span className="mc-muted">{data ? <>거래 {items.length}건 · 소비 <strong>{won(spentTotal)}</strong></> : '거래 불러오는 중'}</span>
        {/* 달력 보기는 기능이 생기면 여기에 [리스트 | 달력] (11/3: 누를 수 없는 버튼이라 뺌) */}
        <button type="button" className="mc-filter-btn" aria-pressed={filterCount > 0} onClick={() => openPopup(
          <FilterPopup categories={CHIPS} pendingCount={pendingCount} categoryCodes={codes} types={types} accountIds={accountIds} cardIds={cardIds} onApply={applyFilter} />,
        )}><Icon name="SlidersHorizontal" size={14} />{filterCount > 0 ? `필터 ${filterCount}` : '필터'}</button>
      </div>

      {/* 처음 받는 중 · 실패. 같은 주소 다시 받기가 실패하면 지난 목록 위에 안내를 띄운다. 다시 시도 중에는 '불러오는 중' */}
      {(!data || (error && !refreshing)) && <ApiStatus error={refreshing ? null : error} onRetry={reload} />}
      {data && refreshing && <p className="mc-muted" role="status">새로 불러오는 중…</p>}
      {data && days.length === 0 && (
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>{needle ? `'${search.trim()}' 이름의 거래가 이 기간에 없어요.` : '이 기간에 거래가 없어요.'}</p></div>
      )}
      {days.map((day) => (
        <div className="mc-card mc-tx-day" key={day.date}>
          <div className="mc-row"><p className="mc-muted" style={{ margin: 0 }}>{dayLabel(day.date)}</p>{day.total > 0 && <span className="mc-muted">{won(day.total)}</span>}</div>
          {day.items.map((tx) =>
            tx.kind === 'pending' ? (
              <div className="mc-tx-pend" key={tx.id}>
                <div className="mc-row">
                  <span className="mc-grow"><strong className="mc-tx-name"><span>{tx.name}</span></strong><span className="mc-pend-badge">카테고리 확인 필요</span></span>
                  <strong className="mc-tx-amt">{won(tx.net)}</strong>
                </div>
                {/* 확인 필요 이유 · 후보 카테고리는 [카테고리 고르기] 팝업이 거래 한 건 보기(17번 suggestion)로 받아 보여 준다 (10/30 A안) */}
                <p className="mc-pend-why">어디에 쓴 돈인가요?</p>
                <div className="mc-pend-pick">
                  <button type="button" onClick={() => openPopup(<TxPickPopup tx={tx} categories={CHIPS} onSaved={refresh} />)}>카테고리 고르기</button>
                </div>
              </div>
            ) : (
              <button type="button" className={`mc-listrow${tx.cancel ? ` is-${tx.cancel}` : ''}${tx.kind !== 'normal' ? ` is-${tx.kind}` : ''}`} key={tx.id}
                onClick={() => openPopup(<TransactionPopup tx={tx} date={dayLabel(day.date)} categories={CHIPS} onSaved={refresh} />)}>
                <span className="mc-tile"><Icon name={tx.icon} /></span>
                <span className="mc-grow">
                  <strong className="mc-tx-name"><span>{tx.name}</span>{tx.funding === 'fixed' && <em className="mc-tx-tag is-fixed">고정</em>}</strong>
                  <span className="mc-muted">{tx.kind === 'income' ? (tx.isSalary ? (tx.salaryAuto ? '월급 · 자동 인식' : '월급') : '입금') : tx.kind === 'transfer' ? `${tx.isSaving ? '저축 · ' : ''}${tx.ownRecheck ? '다시 확인 필요' : OWN_ROW[tx.ownTransfer] ?? (tx.isOwn ? '내 계좌 이체' : '이체')}` : tx.cat}{tx.viaShort && ` · ${tx.viaShort}`}</span>
                </span>
                <span className="mc-right mc-tx-amt">
                  {/* 취소: 금액에 줄 긋고 "취소" / 부분취소: 남은 금액 + 취소된 금액 */}
                  {tx.cancel === 'full' && <><s>{won(tx.amount)}</s> <em className="mc-cancel-tag">취소</em></>}
                  {tx.cancel === 'partial' && <>{won(tx.net)}<small className="mc-cancel-sub">부분취소 −{won(tx.cancelAmount)}</small></>}
                  {!tx.cancel && tx.kind === 'income' && <><span className="mc-tx-in">+{won(tx.amount)}</span> <span className="mc-muted">›</span></>}
                  {!tx.cancel && tx.kind === 'transfer' && <><span className="mc-tx-out">{won(tx.amount)}</span> <span className="mc-muted">›</span></>}
                  {!tx.cancel && tx.kind === 'normal' && <>{won(tx.amount)} <span className="mc-muted">›</span></>}
                </span>
              </button>
            )
          )}
        </div>
      ))}

    </Layout>
  )
}
