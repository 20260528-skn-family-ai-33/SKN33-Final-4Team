import assert from 'node:assert/strict'
import test from 'node:test'
import { cardNoText, nextDigits } from './cardNo.js'

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
