import { PATHS } from '../routes.js'

export function salaryReviewContext(location) {
  const params = new URLSearchParams(location?.search)
  const id = params.get('budgetConversation') || location?.state?.budgetReturn?.conversationId
  const conversationId = typeof id === 'string' && id.length <= 128 ? id.trim() : ''
  return {
    salaryReview: location?.state?.salaryReview === true || params.get('salaryReview') === '1',
    budgetReturn: conversationId ? {
      conversationId,
      // Return only to our chat; never trust a destination supplied in router state.
      returnTo: `${PATHS.assistant}?budgetConversation=${encodeURIComponent(conversationId)}`,
    } : null,
  }
}

export function salaryReviewUrl(path, context) {
  const params = new URLSearchParams()
  if (context.budgetReturn) params.set('budgetConversation', context.budgetReturn.conversationId)
  if (context.salaryReview) params.set('salaryReview', '1')
  return `${path}${params.size ? `?${params}` : ''}`
}
