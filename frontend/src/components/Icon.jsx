// 이름(문자열)으로 아이콘 그리기. mock 데이터에 icon: 'Utensils' 처럼 적어두면 여기서 찾아서 그린다
// 쓰는 아이콘만 골라 가져와야 앱이 가벼워진다. 새 아이콘이 필요하면 아래 두 곳에 이름을 추가!
import {
  Banknote, Building, Bus, ChartNoAxesCombined, ChevronRight, Circle, CircleCheck, CircleMinus, CircleQuestionMark, Compass,
  CreditCard, Ellipsis, Film, GraduationCap, Heart, HeartPulse, House, IdCard, Landmark, Link, Loader,
  MessageCircle, Plane, RefreshCw, Repeat, Scissors, Send, ShieldCheck, Shirt, ShoppingBasket, ShoppingCart, SlidersHorizontal,
  Sparkles, Stethoscope, Store, Ticket, Unlink, UserRound, Users, Utensils, Wallet,
} from 'lucide-react'

const ICONS = {
  Banknote, Building, Bus, ChartNoAxesCombined, ChevronRight, Circle, CircleCheck, CircleMinus, CircleQuestionMark, Compass,
  CreditCard, Ellipsis, Film, GraduationCap, Heart, HeartPulse, House, IdCard, Landmark, Link, Loader,
  MessageCircle, Plane, RefreshCw, Repeat, Scissors, Send, ShieldCheck, Shirt, ShoppingBasket, ShoppingCart, SlidersHorizontal,
  Sparkles, Stethoscope, Store, Ticket, Unlink, UserRound, Users, Utensils, Wallet,
}

// 'credit-card'(장고 API 이름)도 'CreditCard'(리액트 이름)로 바꿔서 찾는다
const toPascal = (name) => name.replace(/(^|-)([a-z0-9])/g, (_, __, c) => c.toUpperCase())

export default function Icon({ name, size = 20 }) {
  const Comp = ICONS[toPascal(name)] ?? Circle
  return <Comp size={size} aria-hidden="true" />
}
