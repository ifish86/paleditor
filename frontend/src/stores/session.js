import { defineStore } from 'pinia'
import { api } from 'boot/api'

export const useSessionStore = defineStore('session', {
  state: () => ({
    role: null,
    expiresAt: null,
    checked: false,
  }),
  getters: {
    isSignedIn: (state) => state.role !== null,
    isOwner: (state) => state.role === 'owner',
  },
  actions: {
    async login (password) {
      const { data } = await api.post('/session', { password })
      this.role = data.role
      this.expiresAt = data.expires_at
      this.checked = true
      return data.role
    },
    async refresh () {
      try {
        const { data } = await api.get('/session')
        this.role = data.role
        this.expiresAt = data.expires_at
      } catch {
        this.role = null
      } finally {
        this.checked = true
      }
    },
    async logout () {
      try {
        await api.delete('/session')
      } finally {
        this.role = null
        this.expiresAt = null
      }
    },
  },
})
