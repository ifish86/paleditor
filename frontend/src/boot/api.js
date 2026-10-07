import axios from 'axios'
import { boot } from 'quasar/wrappers'
import { Notify } from 'quasar'

// Same origin in production: the FastAPI app serves this bundle.
const api = axios.create({ baseURL: '/api', withCredentials: true })

export default boot(({ app, router }) => {
  api.interceptors.response.use(
    (response) => response,
    (error) => {
      const status = error.response?.status
      // An expired session should land on the login screen, not an error toast.
      if (status === 401 && router.currentRoute.value.name !== 'login') {
        router.push({ name: 'login', query: { next: router.currentRoute.value.fullPath } })
        return Promise.reject(error)
      }
      const detail = error.response?.data?.detail
      if (detail && status !== 401) {
        Notify.create({ type: 'negative', message: detail, timeout: 6000, multiLine: true })
      }
      return Promise.reject(error)
    },
  )
  app.config.globalProperties.$api = api
})

export { api }
