import assert from 'node:assert/strict'
import test from 'node:test'
import { caretIndex, cardNoText, editDigits, nextDigits } from './cardNo.js'

test('4자리마다 - , 가리면 앞 4자리만', () => {
  assert.equal(cardNoText('1234567890123456', false), '1234 - •••• - •••• - ••••')
  assert.equal(cardNoText('1234567890123456', true), '1234 - 5678 - 9012 - 3456')
  assert.equal(cardNoText('12345', false), '1234 - •')
  assert.equal(cardNoText('', false), '')
})

test('가린 칸에서 치고 지워도 숫자가 맞다', () => {
  assert.equal(nextDigits('12345', '1234 - •6', false), '123456')          // 끝에 치기
  assert.equal(nextDigits('123456', '1234 - •', false), '12345')           // 끝에서 지우기
  assert.equal(nextDigits('12345678', '1234 - ••9••', false), '123456978')  // 가운데 끼우기
  assert.equal(nextDigits('', '1234 5678-9012-3456', false), '1234567890123456')  // 붙여넣기 (공백 · - 무시)
  assert.equal(nextDigits('1234', '1234 - a', true), '1234')                // 숫자 아닌 글자 무시
  assert.equal(nextDigits('1'.repeat(19), '1'.repeat(19) + '2', true), '1'.repeat(19))  // 19자리까지
})

// 입력 직후 커서 위치(selectionStart)를 같이 넘기는 실제 입력 (PR #101 리뷰: 가린 가운데 자리를 지우면 끝자리가 지워짐)
// '|' = 입력 직후 커서 자리
const edit = (digits, marked, show) => editDigits(digits, marked.replace('|', ''), show, marked.indexOf('|'))
const type = (digits, marked, show) => edit(digits, marked, show).digits

test('가린 가운데 자리를 지우면 그 자리 숫자가 지워진다', () => {
  const no = '1234567890123456'
  // 다섯 번째 자리(첫 •) 뒤에서 Backspace
  assert.equal(type(no, '1234 - |••• - •••• - ••••', false), '123467890123456')
  // 다섯 번째 자리 앞에서 Delete
  assert.equal(type(no, '1234 - |••• - •••• - ••••', false), '123467890123456')
  // 열 번째 자리 뒤에서 Backspace
  assert.equal(type(no, '1234 - •••• - •|•• - ••••', false), '123456789123456')
  // 가린 칸 여러 개를 골라 지우기 (6~8번째)
  assert.equal(type(no, '1234 - •| - •••• - ••••', false), '1234590123456')
  // 가린 칸 하나를 골라 숫자로 바꾸기 (6번째 → 0)
  assert.equal(type(no, '1234 - •0|•• - •••• - ••••', false), '1234507890123456')
  // 보이는 상태에서도 같은 숫자가 이어지면 커서 자리로 찾는다
  assert.equal(type('1111222233334444', '1111 - 2|22 - 3333 - 4444', true), '111122233334444')
})

test('가린 칸 가운데에 끼우기 · 끝에 치기도 커서 기준으로 맞다', () => {
  assert.equal(type('12345678', '1234 - ••9|••', false), '123456978')
  assert.equal(type('12345', '1234 - •6|', false), '123456')
  assert.equal(type('123456', '1234 - •|', false), '12345')
  assert.equal(type('', '1234 5678-9012-3456|', false), '1234567890123456')  // 붙여넣기
})

test('고친 자리 뒤 커서 위치', () => {
  assert.deepEqual(edit('1234567890123456', '1234 - |••• - •••• - ••••', false), { digits: '123467890123456', at: 4 })
  assert.deepEqual(edit('12345678', '1234 - ••9|••', false), { digits: '123456978', at: 7 })
  assert.equal(caretIndex(0), 0)
  assert.equal(caretIndex(4), 4)   // '1234| - '
  assert.equal(caretIndex(5), 8)   // '1234 - 5|'
  assert.equal(caretIndex(8), 11)  // '1234 - 5678|'
  assert.equal(caretIndex(9), 15)  // '1234 - 5678 - 9|'
})

test("' - ' 만 지워지면 지운 방향의 옆 숫자를 지운다", () => {
  // '1234 - |5678' 에서 Backspace → ' ' 가 지워짐 → 앞 숫자 4
  assert.equal(nextDigits('12345678', '1234 -5678', true, 6, 'deleteContentBackward'), '1235678')
  // '1234| - 5678' 에서 Delete → ' ' 가 지워짐 → 뒤 숫자 5
  assert.equal(nextDigits('12345678', '1234- 5678', true, 4, 'deleteContentForward'), '1234678')
  // 가린 칸도 같다
  assert.equal(nextDigits('12345678', '1234- ••••', false, 4, 'deleteContentForward'), '1234678')
  // 입력 종류를 모르면 숫자는 그대로
  assert.equal(nextDigits('12345678', '1234 -5678', true, 6), '12345678')
})
