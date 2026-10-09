<template>
  <q-dialog v-model="open" @hide="$emit('close')">
    <q-card style="min-width: 320px; max-width: 420px">
      <q-card-section>
        <div class="text-subtitle1">Slot {{ slot?.slot_index }}</div>
        <div class="text-caption text-grey-6">
          <template v-if="slot?.item_id">
            Currently {{ slot.display_name }} ×{{ slot.stack_count }}
          </template>
          <template v-else>Currently empty</template>
        </div>
      </q-card-section>

      <q-card-section class="q-pt-none">
        <q-select
          v-model="itemId"
          :options="options"
          option-value="item_id"
          option-label="label"
          emit-value map-options
          use-input input-debounce="120"
          outlined dense clearable
          label="Item"
          @filter="filterItems"
        >
          <template #no-option>
            <q-item><q-item-section class="text-grey-6">No match</q-item-section></q-item>
          </template>

          <!-- The id under the name: two items can read alike, and the id is
               what actually goes into the save. -->
          <template #option="scope">
            <q-item v-bind="scope.itemProps">
              <q-item-section>
                <q-item-label>{{ scope.opt.label }}</q-item-label>
                <q-item-label caption class="text-grey-6">
                  {{ scope.opt.sublabel }}
                </q-item-label>
              </q-item-section>
              <q-item-section v-if="!scope.opt.inWorld" side>
                <q-badge outline color="grey-6" label="not here yet" />
              </q-item-section>
            </q-item>
          </template>
        </q-select>

        <q-input
          v-model.number="stackCount"
          type="number"
          outlined dense
          class="q-mt-sm"
          label="Stack count"
          :disable="!itemId"
          :min="1"
          :max="maxStack || undefined"
          :hint="maxStack ? `Stacks to ${maxStack}` : 'Stack size unknown for this item'"
          :error="Boolean(countError)"
          :error-message="countError"
        />

        <q-banner dense class="bg-warning text-dark q-mt-md">
          <template #avatar><q-icon name="schedule" /></template>
          <!--
            A queued edit does not take effect immediately, and that surprises
            people. Every edit says when it is expected to land.
          -->
          This does not change the chest now. It is applied at the next
          maintenance window, {{ windowLabel }}.
        </q-banner>
      </q-card-section>

      <q-card-actions align="between">
        <q-btn
          flat dense
          color="negative"
          icon="delete_outline"
          label="Empty slot"
          :disable="busy || (!slot?.item_id && !itemId)"
          @click="queue(true)"
        />
        <div>
          <q-btn flat label="Cancel" v-close-popup :disable="busy" />
          <q-btn
            color="primary"
            label="Queue edit"
            :loading="busy"
            :disable="!itemId || Boolean(countError)"
            @click="queue(false)"
          />
        </div>
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api } from 'boot/api'
import { useCatalogStore } from 'stores/catalog'
import { useStatusStore } from 'stores/status'
import { formatWindow } from 'src/util/format'
import { Notify } from 'quasar'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  slot: { type: Object, default: null },
  containerGuid: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue', 'queued', 'close'])

const catalog = useCatalogStore()
const status = useStatusStore()
const itemId = ref(null)
const stackCount = ref(1)
const busy = ref(false)
const needle = ref('')

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const windowLabel = computed(() => formatWindow(status.nextWindow))
const maxStack = computed(() => (itemId.value ? catalog.maxStack(itemId.value) : null))

const countError = computed(() => {
  if (!itemId.value) return ''
  if (!Number.isInteger(stackCount.value) || stackCount.value < 1) {
    return 'Must be at least 1'
  }
  if (maxStack.value && stackCount.value > maxStack.value) {
    return `${itemId.value} stacks to ${maxStack.value}`
  }
  return ''
})

const LIMIT = 60

function toOption (item) {
  return {
    item_id: item.item_id,
    label: item.display_name || item.item_id,
    // The internal id, shown underneath: two items can read alike, and the id
    // is what actually goes into the save.
    sublabel: item.item_id,
    // Whether this world already contains any. The picker offers the game's
    // whole catalogue, so most entries are things no chest here has held.
    inWorld: Boolean(item.in_world),
  }
}

const options = computed(() => {
  const term = needle.value.toLowerCase()
  const matches = catalog.items
    .filter((item) =>
      !term ||
      item.item_id.toLowerCase().includes(term) ||
      (item.display_name || '').toLowerCase().includes(term),
    )
    .slice(0, LIMIT)
    .map(toOption)

  // The selected item has to be among the options or QSelect has no label to
  // render for it and the field shows blank. With a few hundred items the
  // slice almost never contains whatever the slot already held.
  if (itemId.value && !matches.some((o) => o.item_id === itemId.value)) {
    const current = catalog.items.find((i) => i.item_id === itemId.value)
    matches.unshift(current
      ? toOption(current)
      : { item_id: itemId.value, label: itemId.value, sublabel: itemId.value })
  }
  return matches
})

// immediate, because the dialog is created with v-if once a slot is chosen:
// the prop is already set on the first render, so a deferred watch never fires
// and the field opens blank instead of showing what the slot holds.
watch(() => props.slot, (slot) => {
  itemId.value = slot?.item_id || null
  stackCount.value = slot?.stack_count || 1
  needle.value = ''
}, { immediate: true })

function filterItems (value, update) {
  update(() => { needle.value = value })
}

async function queue (clear) {
  busy.value = true
  try {
    const payload = clear
      ? { slot_index: props.slot.slot_index, item_id: null, stack_count: 0 }
      : {
          slot_index: props.slot.slot_index,
          item_id: itemId.value,
          stack_count: stackCount.value,
        }
    const { data } = await api.post(`/chests/${props.containerGuid}/edits`, payload)
    Notify.create({
      type: 'positive',
      message: `Queued. Lands ${formatWindow(data.expected_window)}.`,
    })
    emit('queued')
    open.value = false
  } catch {
    // The interceptor has already shown the server's reason.
  } finally {
    busy.value = false
  }
}
</script>
