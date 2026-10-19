// 돈 표시 도우미 (장고 budgets/templatetags/money.py 와 같은 역할)
// comma(2800000) → "2,800,000" / won(2800000) → "2,800,000원"
export const comma = (n) => Number(n).toLocaleString('ko-KR')
export const won = (n) => `${comma(n)}원`
