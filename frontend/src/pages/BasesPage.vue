<template>
  <q-page class="q-pa-md">
    <q-inner-loading :showing="loading" />

    <div v-if="lastIngest" class="text-caption text-grey-6 q-mb-md">
      {{ bases.length }} bases ·
      last read {{ relativeTime(lastIngest.finished_at) }}
      <span v-if="status.nextWindow">
        · next window {{ formatWindow(status.nextWindow) }}
      </span>
    </div>

    <div class="row q-col-gutter-md">
      <div
        v-for="base in allCards"
        :key="base.base_guid || 'unassigned'"
        class="col-12 col-sm-6 col-md-4"
      >
        <q-card
          flat bordered
          class="cursor-pointer"
          @click="open(base)"
        >
          <q-card-section>
            <div class="row items-center no-wrap">
              <div class="col">
                <div class="text-subtitle1 text-weight-medium ellipsis">
                  {{ base.name || shortGuid(base.base_guid) }}
                </div>
                <div class="text-caption text-grey-6">
                  {{ base.chest_count }} chest{{ base.chest_count === 1 ? '' : 's' }}
                </div>
              </div>
              <q-icon name="chevron_right" size="sm" color="grey-6" />
            </div>
          </q-card-section>

          <q-card-section class="q-pt-none">
            <q-chip
              v-if="!status.contentsOnly && base.locked_count"
              dense square size="sm" icon="lock" color="grey-8" text-color="white"
            >
              {{ base.locked_count }} locked
            </q-chip>
            <q-chip
              v-if="base.pending_chests"
              dense square size="sm" icon="pending" color="warning" text-color="dark"
            >
              {{ base.pending_chests }} with queued edits
            </q-chip>
            <q-chip
              v-if="base.stale"
              dense square size="sm" icon="history" color="grey-7" text-color="white"
            >
              not in the latest read
            </q-chip>
          </q-card-section>
        </q-card>
      </div>
    </div>

    <q-banner v-if="!loading && !allCards.length" class="bg-grey-9 text-white q-mt-md">
      <template #avatar><q-icon name="inbox" /></template>
      No bases yet. Run an ingest to read the save.
    </q-banner>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from 'boot/api'
import { useStatusStore } from 'stores/status'
import { relativeTime, formatWindow, shortGuid } from 'src/util/format'

const bases = ref([])
const unassigned = ref(null)
const lastIngest = ref(null)
const loading = ref(true)
const router = useRouter()
const status = useStatusStore()

// Chests out of range of any base camp are still reachable, as their own card.
const allCards = computed(() => {
  const cards = [...bases.value]
  if (unassigned.value) cards.push({ ...unassigned.value, stale: false })
  return cards
})

onMounted(async () => {
  try {
    const { data } = await api.get('/bases')
    bases.value = data.bases
    unassigned.value = data.unassigned || null
    lastIngest.value = data.last_ingest
  } finally {
    loading.value = false
  }
})

function open (base) {
  router.push({
    name: 'chests',
    params: { baseGuid: base.base_guid || 'unassigned' },
  })
}
</script>
