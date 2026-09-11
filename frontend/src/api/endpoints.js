import { api } from './client.js'

export const systemApi = {
  health: () => api.get('/health'),
  meta: () => api.get('/meta'),
}

export const authApi = {
  login: (payload) => api.post('/auth/login', payload),
  register: (payload) => api.post('/auth/register', payload),
  demoLogin: () => api.post('/auth/demo-login'),
  me: () => api.get('/auth/me'),
}

export const characterApi = {
  list: () => api.get('/characters'),
  get: (id) => api.get('/characters/' + id),
  create: (payload) => api.post('/characters', payload),
  update: (id, payload) => api.put('/characters/' + id, payload),
  remove: (id) => api.delete('/characters/' + id),
}

export const knowledgeApi = {
  list: () => api.get('/knowledge'),
  create: (payload) => api.post('/knowledge', payload),
  remove: (id) => api.delete('/knowledge/' + id),
  documents: (id, limit) => api.get('/knowledge/' + id + '/documents', { params: { limit: limit || 50 } }),
  upload: (file, options) => {
    const form = new FormData()
    form.append('file', file)
    const opts = options || {}
    if (opts.knowledgeId) form.append('knowledge_id', String(opts.knowledgeId))
    if (opts.name) form.append('name', opts.name)
    if (opts.description) form.append('description', opts.description)
    return api.post('/knowledge/upload', form)
  },
}

export const chatApi = {
  send: (payload) => api.post('/chat', payload),
  history: (params) => api.get('/chat/history', { params }),
  conversations: (params) => api.get('/chat/conversations', { params }),
  removeConversation: (id) => api.delete('/chat/conversations/' + id),
}

export const roundtableApi = {
  agents: () => api.get('/roundtable/agents'),
  run: (payload) => api.post('/roundtable', payload),
}

export const memoryApi = {
  list: (params) => api.get('/memories', { params }),
  create: (payload) => api.post('/memories', payload),
  remove: (id) => api.delete('/memories/' + id),
}
