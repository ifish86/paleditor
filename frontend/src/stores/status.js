import { defineStore } from 'pinia'
import { api } from 'boot/api'

export const useStatusStore = defineStore('status', {
  state: () => ({
    serverRunning: null,
    lastIngest: null,
    lastGoodIngest: null,
    nextWindow: null,
    queue: {},
    lockCodesAvailable: true,
    stale: false,
    windowInProgress: false,
    saveBackend: null,
    backendAvailable: true,
    warnings: [],
    loading: false,
  }),
  getters: {
    queuedCount: (state) => state.queue.queued ?? 0,
    failedCount: (state) => state.queue.failed ?? 0,
    // Drives the contents-only mode: with no lock-code field, showing every
    // chest as unlocked would be a lie.
    contentsOnly: (state) => !state.lockCodesAvailable,
  },
  actions: {
    async load () {
      this.loading = true
      try {
        const { data } = await api.get('/status')
        this.serverRunning = data.server_running
        this.lastIngest = data.last_ingest
        this.lastGoodIngest = data.last_good_ingest
        this.nextWindow = data.next_window
        this.queue = data.queue
        this.lockCodesAvailable = data.lock_codes_available
        this.stale = data.stale
        this.windowInProgress = data.window_in_progress
        this.saveBackend = data.save_backend
        this.backendAvailable = data.backend_available
        this.warnings = data.warnings || []
      } finally {
        this.loading = false
      }
    },
  },
})
