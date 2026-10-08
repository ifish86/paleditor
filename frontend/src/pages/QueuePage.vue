<template>
  <q-page class="q-pa-md">
    <div class="row q-col-gutter-md q-mb-md">
      <div class="col-12 col-md-7">
        <q-card flat bordered>
          <q-card-section>
            <div class="text-subtitle2">Status</div>
          </q-card-section>
          <q-list dense>
            <q-item>
              <q-item-section>Game server</q-item-section>
              <q-item-section side>
                <q-badge
                  :color="status.serverRunning === null ? 'grey-7'
                    : status.serverRunning ? 'positive' : 'negative'"
                  :label="status.serverRunning === null ? 'unknown'
                    : status.serverRunning ? 'up' : 'down'"
                />
              </q-item-section>
            </q-item>
            <q-item>
              <q-item-section>Last read of the save</q-item-section>
              <q-item-section side>
                {{ relativeTime(status.lastGoodIngest?.finished_at) }}
              </q-item-section>
            </q-item>
            <q-item>
              <q-item-section>Next maintenance window</q-item-section>
              <q-item-section side>{{ formatWindow(status.nextWindow) }}</q-item-section>
            </q-item>
            <q-item>
              <q-item-section>Queue depth</q-item-section>
              <q-item-section side>{{ status.queuedCount }} waiting</q-item-section>
            </q-item>
          </q-list>
        </q-card>
      </div>

      <div class="col-12 col-md-5">
        <q-card flat bordered>
          <q-card-section>
            <div class="text-subtitle2">Run the window now</div>
            <div class="text-caption text-grey-6 q-mt-xs">
              Stops the game server, applies {{ status.queuedCount }} queued
              edit{{ status.queuedCount === 1 ? '' : 's' }} and restarts it.
            </div>
          </q-card-section>
          <!--
            Say why the button will not work before it is pressed. The window
            runs in the background, so a refusal used to arrive as a cheerful
            "started" followed by nothing at all.
          -->
          <q-card-section v-if="status.windowBlocked" class="q-pt-none">
            <q-banner dense class="bg-grey-9 text-white">
              <template #avatar><q-icon name="block" color="warning" /></template>
              <div class="text-caption">{{ status.windowBlocked }}</div>
            </q-banner>
          </q-card-section>

          <q-card-actions>
            <q-btn
              color="negative"
              icon="build"
              label="Run now"
              :loading="running"
              :disable="!session.isOwner || status.windowInProgress || Boolean(status.windowBlocked)"
              @click="confirmRun"
            />
          </q-card-actions>
          <q-card-section v-if="!session.isOwner" class="q-pt-none text-caption text-grey-6">
            Needs the owner password.
          </q-card-section>

          <!-- What the previous run actually did. -->
          <q-card-section v-if="lastRun" class="q-pt-none text-caption">
            <q-separator class="q-mb-sm" />
            <div class="text-grey-6">
              Last window {{ relativeTime(lastRun.finished_at) }}:
              <span :class="lastRun.ok ? 'text-positive' : 'text-negative'">
                {{ lastRun.ok ? 'completed' : 'failed' }}
              </span>
              <template v-if="lastRun.claimed">
                - {{ lastRun.applied }} applied, {{ lastRun.failed }} failed
              </template>
              <template v-if="lastRun.restored"> &middot; backup restored</template>
            </div>
            <div v-if="lastRun.error" class="text-negative q-mt-xs">
              {{ lastRun.error }}
            </div>
          </q-card-section>
        </q-card>
      </div>
    </div>

    <q-table
      :rows="edits"
      :columns="columns"
      row-key="id"
      flat bordered
      :loading="loading"
      :pagination="{ rowsPerPage: 25 }"
      :filter="statusFilter"
      no-data-label="Nothing has been queued"
    >
      <template #top>
        <div class="text-subtitle2 q-mr-md">Edit queue</div>
        <q-space />
        <q-select
          v-model="statusFilter"
          :options="statusOptions"
          dense outlined clearable
          emit-value map-options
          label="Status"
          style="min-width: 150px"
          @update:model-value="load"
        />
      </template>

      <template #body-cell-status="props">
        <q-td :props="props">
          <q-badge
            :color="EDIT_STATUS_COLORS[props.value] || 'grey'"
            :text-color="['queued', 'applied'].includes(props.value) ? 'dark' : 'white'"
            :label="props.value"
          />
        </q-td>
      </template>

      <template #body-cell-change="props">
        <q-td :props="props">
          <template v-if="props.row.is_clear">
            <span class="text-negative">empty the slot</span>
          </template>
          <template v-else>
            {{ props.row.display_name }} ×{{ props.row.stack_count }}
          </template>
        </q-td>
      </template>

      <template #body-cell-actions="props">
        <q-td :props="props">
          <q-btn
            v-if="props.row.status === 'queued'"
            flat dense round icon="close"
            aria-label="Cancel"
            @click="cancel(props.row)"
          />
          <!-- A failed edit keeps its error and stays visible, not retried. -->
          <q-icon
            v-else-if="props.row.error"
            name="error_outline" color="negative"
          >
            <q-tooltip class="text-body2" max-width="320px">
              {{ props.row.error }}
            </q-tooltip>
          </q-icon>
        </q-td>
      </template>
    </q-table>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Dialog, Notify } from 'quasar'
import { api } from 'boot/api'
import { useSessionStore } from 'stores/session'
import { useStatusStore } from 'stores/status'
import { EDIT_STATUS_COLORS, formatWindow, relativeTime } from 'src/util/format'

const session = useSessionStore()
const status = useStatusStore()
const edits = ref([])
const loading = ref(false)
const running = ref(false)
const statusFilter = ref(null)

// Only show a previous run once one has actually happened; being blocked is
// not a run.
const lastRun = computed(() =>
  status.lastWindow && status.lastWindow.finished_at ? status.lastWindow : null,
)

const statusOptions = [
  { label: 'Queued', value: 'queued' },
  { label: 'Applying', value: 'applying' },
  { label: 'Applied', value: 'applied' },
  { label: 'Failed', value: 'failed' },
  { label: 'Cancelled', value: 'cancelled' },
]

const columns = [
  { name: 'status', label: 'Status', field: 'status', align: 'left' },
  {
    name: 'chest',
    label: 'Chest',
    align: 'left',
    field: (row) => row.nickname || row.container_guid.slice(0, 8),
  },
  { name: 'slot', label: 'Slot', field: 'slot_index', align: 'right' },
  { name: 'change', label: 'Change', field: 'item_id', align: 'left' },
  { name: 'by', label: 'By', field: 'requested_by', align: 'left' },
  {
    name: 'when',
    label: 'Requested',
    align: 'left',
    field: (row) => relativeTime(row.requested_at),
  },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

async function load () {
  loading.value = true
  try {
    const params = statusFilter.value ? { status: statusFilter.value } : {}
    const { data } = await api.get('/edits', { params })
    edits.value = data.edits
  } finally {
    loading.value = false
  }
  status.load()
}

onMounted(load)

function confirmRun () {
  Dialog.create({
    title: 'Run the maintenance window?',
    message:
      `This stops the game server, applies ${status.queuedCount} queued edit(s) ` +
      'and starts it again. Anyone online gets a countdown first.',
    cancel: true,
    ok: { label: 'Run it', color: 'negative' },
  }).onOk(run)
}

async function run () {
  running.value = true
  try {
    await api.post('/maintenance/run', null, { params: { confirm: true } })
    Notify.create({
      type: 'info',
      message: 'The window is running. This page updates as it progresses.',
      timeout: 8000,
    })
    setTimeout(load, 4000)
  } catch {
    // The interceptor reports the server's reason.
  } finally {
    running.value = false
  }
}

async function cancel (edit) {
  await api.delete(`/edits/${edit.id}`)
  Notify.create({ type: 'info', message: 'Edit cancelled' })
  await load()
}
</script>
