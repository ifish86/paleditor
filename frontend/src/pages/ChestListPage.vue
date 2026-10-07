<template>
  <q-page class="q-pa-md">
    <q-inner-loading :showing="loading" />

    <div class="text-h6 q-mb-xs">{{ base?.name || shortGuid(base?.base_guid) }}</div>
    <div class="text-caption text-grey-6 q-mb-md">
      showing {{ chests.length }} of {{ total }}
      <q-toggle
        v-model="includeLoot"
        dense size="sm"
        label="include world loot"
        class="q-ml-md"
        @update:model-value="reload"
      />
    </div>

    <q-input
      v-model="filter"
      dense outlined clearable
      placeholder="Filter by nickname, code or item"
      class="q-mb-md"
    >
      <template #prepend><q-icon name="filter_list" /></template>
    </q-input>

    <q-list bordered separator>
      <q-item
        v-for="chest in filtered"
        :key="chest.container_guid"
        clickable
        :to="{ name: 'chest', params: { containerGuid: chest.container_guid } }"
      >
        <q-item-section>
          <q-item-label class="text-weight-medium">
            {{ chest.nickname || label(chest) }}
            <q-badge
              v-if="chest.kind === 'lock-only'"
              outline color="orange" label="door" class="q-ml-xs"
            />
            <q-badge
              v-else-if="chest.kind === 'loot'"
              outline color="grey-6" label="world loot" class="q-ml-xs"
            />
            <q-badge
              v-else-if="!chest.nickname"
              outline color="grey-6" label="no nickname" class="q-ml-xs"
            />
          </q-item-label>
          <q-item-label caption>
            <span v-if="!status.contentsOnly && chest.lock_code" class="lock-code">
              <q-icon name="lock" size="12px" /> {{ chest.lock_code }} ·
            </span>
            <!-- slot_count is capacity; only occupied slots are stored. -->
            <span v-if="chest.has_container">
              {{ chest.filled_slots }}/{{ chest.slot_count }} slots ·
            </span>
            <span v-else>holds nothing · </span>
            {{ coordinates(chest) }}
          </q-item-label>
        </q-item-section>

        <q-item-section side>
          <div class="row items-center q-gutter-xs">
            <q-badge
              v-if="chest.pending_count"
              color="warning" text-color="dark"
              :label="`${chest.pending_count} queued`"
            />
            <q-icon v-if="chest.stale" name="history" color="grey-6" size="sm">
              <q-tooltip>Not seen in the latest read of the save</q-tooltip>
            </q-icon>
            <q-linear-progress
              v-if="chest.has_container"
              :value="chest.fill_ratio"
              style="width: 42px"
              size="6px"
              rounded
              :color="chest.fill_ratio > 0.85 ? 'negative' : 'primary'"
            />
          </div>
        </q-item-section>
      </q-item>
    </q-list>

    <div v-if="hasMore" class="row justify-center q-mt-md">
      <q-btn
        flat color="primary" :loading="loading"
        :label="`Load ${Math.min(PAGE, total - chests.length)} more`"
        @click="loadMore"
      />
    </div>

    <q-banner v-if="!loading && !chests.length" class="bg-grey-9 text-white q-mt-md">
      No chests at this base.
    </q-banner>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from 'boot/api'
import { useStatusStore } from 'stores/status'
import { coordinates, shortGuid } from 'src/util/format'

const route = useRoute()
const status = useStatusStore()
const PAGE = 100

const base = ref(null)
const chests = ref([])
const filter = ref('')
const loading = ref(true)
const total = ref(0)
const hasMore = ref(false)
// World loot outnumbers player storage many times over, so it is off by
// default and counted rather than listed.
const includeLoot = ref(false)

const filtered = computed(() => {
  const term = (filter.value || '').trim().toLowerCase()
  if (!term) return chests.value
  return chests.value.filter((chest) =>
    (chest.nickname || '').toLowerCase().includes(term) ||
    (chest.lock_code || '').includes(term) ||
    chest.container_guid.includes(term),
  )
})

function label (chest) {
  return chest.object_type || shortGuid(chest.container_guid)
}

async function fetchPage (offset) {
  const params = { limit: PAGE, offset }
  if (includeLoot.value) params.kinds = 'all'
  const { data } = await api.get(`/bases/${route.params.baseGuid}/chests`, { params })
  base.value = data.base
  total.value = data.total
  hasMore.value = data.has_more
  return data.chests
}

async function reload () {
  loading.value = true
  try {
    chests.value = await fetchPage(0)
  } finally {
    loading.value = false
  }
}

async function loadMore () {
  loading.value = true
  try {
    chests.value = chests.value.concat(await fetchPage(chests.value.length))
  } finally {
    loading.value = false
  }
}

onMounted(reload)
watch(() => route.params.baseGuid, reload)
</script>
