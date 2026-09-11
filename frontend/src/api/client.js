import axios from 'axios'

export const TOKEN_KEY = 'aiworld_token'
export const USER_KEY = 'aiworld_user'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 180000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = 'Bearer ' + token
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error && error.response ? error.response.status : 0
    if (status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export function extractError(error) {
  if (error && error.response && error.response.data) {
    const data = error.response.data
    if (typeof data.detail === 'string') return data.detail
    if (Array.isArray(data.errors) && data.errors.length > 0) {
      const first = data.errors[0]
      if (first && typeof first.msg === 'string') return first.msg
    }
    if (Array.isArray(data.detail) && data.detail.length > 0) {
      const first = data.detail[0]
      if (first && typeof first.msg === 'string') return first.msg
    }
  }
  if (error && error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
  if (error && error.message) return error.message
  return '请求失败，请稍后重试'
}
