<template>
  <q-page class="q-pa-md">
    <q-input
      v-model="term"
      autofocus dense outlined clearable
      placeholder="Nickname, lock code, or an item inside"
      @update:model-value="search"
    >
      <template #prepend><q-icon name="search" /></template>
    </q-input>

    <div class="text-caption text-grey-6 q-mt-sm q-mb-md">
      Searches across nicknames, lock codes and chest contents.
    </div>

    <q-inner-loading :showing="loading" />

    <q-list v-if="results.length" bordered separator>
      <q-item
        v-for="chest in results"
        :key="chest.container_guid"
        clickable
        :to="{ name: 'chest', params: { containerGuid: chest.container_guid } }"
      >
        <q-item-section>
          <q-item-label>
            {{ chest.nickname || shortGuid(chest.container_guid) }}
          </q-item-label>
          <q-item-label caption>
            {{ chest.base_name || 'Not near a base' }} ·
            <span v-if="!status.contentsOnly && chest.lock_code" class="lock-code">
              {{ chest.lock_code }} ·
            </span>
            {{ chest.filled_slots }}/{{ chest.slot_count }}
          </q-item-label>
        </q-item-section>
        <q-item-section side>
          <q-badge
            v-if="chest.pending_count"
            color="warning" text-color="dark"
            :label="`${chest.pending_count} queued`"
          />
        </q-item-section>
      </q-item>
    </q-list>

    <q-banner
      v-else-if="term && !loading"
      class="bg-grey-9 text-white"
    >
      Nothing matched “{{ term }}”.
    </q-banner>
  </q-page>
</template>

<script setup>
import { ref } from 'vue'
import { api } from 'boot/api'
import { useStatusStore } from 'stores/status'
import { shortGuid } from 'src/util/format'

const status = useStatusStore()
const term = ref('')
const results = ref([])
const loading = ref(false)
let timer = null

function search () {
  clearTimeout(timer)
  const value = (term.value || '').trim()
  if (!value) {
    results.value = []
    return
  }
  timer = setTimeout(async () => {
    loading.value = true
    try {
      const { data } = await api.get('/chests/search', { params: { q: value } })
      results.value = data.chests
    } finally {
      loading.value = false
    }
  }, 250)
}
</script>
