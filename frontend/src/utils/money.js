// 돈 표시 도우미: comma(420000) → '420,000' / won(420000) → '420,000원'
// comma(2800000) → "2,800,000" / won(2800000) → "2,800,000원"
export const comma = (n) => Number(n).toLocaleString('ko-KR')
export const won = (n) => `${comma(n)}원`
