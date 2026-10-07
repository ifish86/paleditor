import { defineStore } from 'pinia'
import { api } from 'boot/api'

// The item catalogue is small and changes only on ingest, so it is fetched
// once and kept for the item picker.
export const useCatalogStore = defineStore('catalog', {
  state: () => ({
    items: [],
    loaded: false,
  }),
  getters: {
    byId: (state) => Object.fromEntries(state.items.map((item) => [item.item_id, item])),
    // An id the catalogue does not know still shows as its raw string.
    displayName: (state) => (itemId) => {
      if (!itemId) return null
      const hit = state.items.find((item) => item.item_id === itemId)
      return hit?.display_name || itemId
    },
  },
  actions: {
    async load (force = false) {
      if (this.loaded && !force) return
      const { data } = await api.get('/items')
      this.items = data.items
      this.loaded = true
    },
    maxStack (itemId) {
      return this.byId[itemId]?.max_stack ?? null
    },
  },
})
