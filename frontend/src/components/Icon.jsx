// 이름(문자열)으로 아이콘 그리기. mock 데이터에 icon: 'Utensils' 처럼 적어두면 여기서 찾아서 그린다
// 쓰는 아이콘만 골라 가져와야 앱이 가벼워진다. 새 아이콘이 필요하면 아래 두 곳에 이름을 추가!
import {
  Building, Bus, ChartNoAxesCombined, ChevronRight, Circle, CreditCard, Film, Heart, HeartPulse,
  House, Landmark, MessageCircle, Repeat, Scissors, ShoppingBasket, ShoppingCart, SlidersHorizontal,
  Sparkles, Store, Users, Utensils,
} from 'lucide-react'

const ICONS = {
  Building, Bus, ChartNoAxesCombined, ChevronRight, Circle, CreditCard, Film, Heart, HeartPulse,
  House, Landmark, MessageCircle, Repeat, Scissors, ShoppingBasket, ShoppingCart, SlidersHorizontal,
  Sparkles, Store, Users, Utensils,
}

export default function Icon({ name, size = 20 }) {
  const Comp = ICONS[name] ?? Circle
  return <Comp size={size} aria-hidden="true" />
}
