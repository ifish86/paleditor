<template>
  <q-page class="q-pa-md">
    <q-inner-loading :showing="loading" />

    <template v-if="chest">
      <!-- Nickname and notes are editable inline at the top. -->
      <q-card flat bordered class="q-mb-md">
        <q-card-section class="q-pb-none">
          <q-input
            v-model="nickname"
            dense borderless
            class="text-h6"
            placeholder="Add a nickname"
            maxlength="60"
            @blur="saveMeta"
            @keyup.enter="saveMeta"
          />
          <q-input
            v-model="notes"
            dense borderless autogrow
            class="text-caption"
            placeholder="Notes"
            maxlength="2000"
            @blur="saveMeta"
          />
        </q-card-section>

        <q-card-section class="text-caption text-grey-6">
          <div class="row q-col-gutter-md">
            <div v-if="!status.contentsOnly">
              <q-icon name="lock" size="14px" />
              <span class="lock-code q-ml-xs">{{ chest.lock_code || 'no code' }}</span>
            </div>
            <div><q-icon name="place" size="14px" /> {{ coordinates(chest) }}</div>
            <div>
              <q-icon name="inventory_2" size="14px" />
              {{ chest.filled_slots }}/{{ chest.slot_count }}
            </div>
            <div v-if="chest.base_name">
              <q-icon name="home" size="14px" /> {{ chest.base_name }}
            </div>
          </div>
          <div class="q-mt-xs text-grey-7">{{ chest.container_guid }}</div>
        </q-card-section>
      </q-card>

      <q-banner v-if="chest.stale" dense class="bg-grey-8 text-white q-mb-md">
        <template #avatar><q-icon name="history" /></template>
        This chest was not in the latest read of the save. It may have been
        dismantled.
      </q-banner>

      <q-banner
        v-if="chest.pending_edits.length"
        dense
        class="bg-warning text-dark q-mb-md"
      >
        <template #avatar><q-icon name="schedule" /></template>
        {{ chest.pending_edits.length }} queued
        edit{{ chest.pending_edits.length === 1 ? '' : 's' }}, landing
        {{ formatWindow(status.nextWindow) }}. Marked slots below show what will
        change.
      </q-banner>

      <SlotGrid :slots="chest.slots" @select="openEditor" />

      <q-list v-if="chest.pending_edits.length" bordered class="q-mt-md">
        <q-item-label header>Queued for this chest</q-item-label>
        <q-item v-for="edit in chest.pending_edits" :key="edit.id">
          <q-item-section>
            <q-item-label>
              Slot {{ edit.slot_index }}:
              {{ edit.is_clear ? 'empty it' : `${edit.display_name} ×${edit.stack_count}` }}
            </q-item-label>
            <q-item-label caption>
              by {{ edit.requested_by }} · {{ relativeTime(edit.requested_at) }}
            </q-item-label>
          </q-item-section>
          <q-item-section side>
            <q-btn
              flat dense round icon="close"
              aria-label="Cancel this edit"
              @click="cancel(edit)"
            />
          </q-item-section>
        </q-item>
      </q-list>
    </template>

    <SlotEditDialog
      v-if="chest && selected"
      v-model="editing"
      :slot="selected"
      :container-guid="chest.container_guid"
      @queued="load"
      @close="selected = null"
    />
  </q-page>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Dialog, Notify } from 'quasar'
import { api } from 'boot/api'
import { useStatusStore } from 'stores/status'
import { coordinates, formatWindow, relativeTime } from 'src/util/format'
import SlotGrid from 'components/SlotGrid.vue'
import SlotEditDialog from 'components/SlotEditDialog.vue'

const route = useRoute()
const status = useStatusStore()
const chest = ref(null)
const loading = ref(true)
const nickname = ref('')
const notes = ref('')
const selected = ref(null)
const editing = ref(false)

async function load () {
  loading.value = true
  try {
    const { data } = await api.get(`/chests/${route.params.containerGuid}`)
    chest.value = data
    nickname.value = data.nickname || ''
    notes.value = data.notes || ''
  } finally {
    loading.value = false
  }
  status.load()
}

onMounted(load)
watch(() => route.params.containerGuid, load)

function openEditor (slot) {
  if (slot.has_pending) {
    // A slot with an edit already queued cannot take a second one, so offer to
    // cancel the existing edit rather than failing on submit.
    const edit = chest.value.pending_edits.find((e) => e.slot_index === slot.slot_index)
    Dialog.create({
      title: `Slot ${slot.slot_index}`,
      message: edit
        ? `Already queued: ${edit.is_clear ? 'empty it' : `${edit.display_name} ×${edit.stack_count}`}. Cancel it?`
        : 'This slot already has a queued edit.',
      cancel: true,
      ok: { label: 'Cancel the edit', color: 'negative', flat: true },
    }).onOk(() => edit && cancel(edit))
    return
  }
  selected.value = slot
  editing.value = true
}

async function saveMeta () {
  const current = chest.value
  if (!current) return
  const next = { nickname: nickname.value || null, notes: notes.value || null }
  if ((current.nickname || null) === next.nickname &&
      (current.notes || null) === next.notes) {
    return
  }
  const { data } = await api.patch(`/chests/${current.container_guid}/meta`, next)
  chest.value = { ...current, nickname: data.nickname, notes: data.notes }
  Notify.create({ type: 'positive', message: 'Saved', timeout: 1200 })
}

async function cancel (edit) {
  await api.delete(`/edits/${edit.id}`)
  Notify.create({ type: 'info', message: 'Edit cancelled' })
  await load()
}
</script>
