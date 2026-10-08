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
    lastWindow: null,
    loading: false,
    // Polling handle and the interval currently in use, so a change of pace
    // can be applied without stacking timers.
    _timer: null,
    _everyMs: null,
  }),
  getters: {
    queuedCount: (state) => state.queue.queued ?? 0,
    failedCount: (state) => state.queue.failed ?? 0,
    // Drives the contents-only mode: with no lock-code field, showing every
    // chest as unlocked would be a lie.
    contentsOnly: (state) => !state.lockCodesAvailable,
    // Why the window would refuse, if it would. This governs whether queued
    // edits can ever land, so it is worth showing before anyone presses the
    // button rather than after.
    windowBlocked: (state) => (state.lastWindow && state.lastWindow.blocked) || null,
  },
  actions: {
    // A window takes a minute or two, during which the server goes down, the
    // edit moves to 'applying' and then to 'applied'. Loading once on mount
    // meant watching none of that happen.
    pollIntervalMs () {
      return this.windowInProgress ? 3000 : 30000
    },

    startPolling () {
      if (this._timer) return
      this._tick()
    },

    stopPolling () {
      if (this._timer) {
        clearTimeout(this._timer)
        this._timer = null
        this._everyMs = null
      }
    },

    async _tick () {
      // Nothing to learn while the tab is hidden, and it would keep a phone
      // awake for no reason.
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
        try {
          await this.load()
        } catch {
          // Transient failures are normal during a window: the API is up but
          // the machine is busy. Keep polling rather than giving up.
        }
      }
      this._everyMs = this.pollIntervalMs()
      this._timer = setTimeout(() => this._tick(), this._everyMs)
    },

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
        this.lastWindow = data.last_window || null
      } finally {
        this.loading = false
      }
    },
  },
})
