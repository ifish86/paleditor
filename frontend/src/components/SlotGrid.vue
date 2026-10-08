<template>
  <div class="slot-grid">
    <div
      v-for="slot in slots"
      :key="slot.slot_index"
      class="slot-cell"
      :class="[
        categoryClass(slot),
        { 'is-empty': !slot.item_id && !slot.has_pending, 'has-pending': slot.has_pending },
      ]"
      role="button"
      :aria-label="`Slot ${slot.slot_index}`"
      @click="$emit('select', slot)"
    >
      <!--
        An icon when one was found, otherwise the category stripe carries the
        meaning. Most items have no icon: the wiki keys images on display
        names and the save keys items on internal ids.
      -->
      <img
        v-if="slot.item_id && !iconFailed[slot.item_id]"
        class="slot-icon"
        :src="`/api/items/${encodeURIComponent(slot.item_id)}/icon`"
        :alt="slot.display_name"
        loading="lazy"
        @error="iconFailed[slot.item_id] = true"
      />

      <div class="slot-item-name">
        <!--
          A queued edit renders over the slot it affects, so the pending state
          is visible where the change will happen rather than only in a list.
        -->
        <template v-if="slot.has_pending">
          <div class="text-warning text-weight-medium">
            {{ pendingLabel(slot) }}
          </div>
          <div v-if="slot.item_id" class="text-grey-6" style="text-decoration: line-through">
            {{ slot.display_name }}
          </div>
        </template>
        <template v-else-if="slot.item_id">
          {{ slot.display_name }}
        </template>
        <template v-else>
          <!--
            Empty slots are not stored in the save at all; the API fills the
            grid out to the container's capacity so every slot is addressable.
          -->
          <span class="text-grey-7">empty</span>
        </template>
      </div>

      <div class="row items-end justify-between no-wrap">
        <div class="text-caption text-grey-7">{{ slot.slot_index }}</div>
        <div class="slot-count">
          <template v-if="slot.has_pending">
            <span class="text-warning">{{ slot.pending_stack_count || '—' }}</span>
          </template>
          <template v-else-if="slot.stack_count">{{ slot.stack_count }}</template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive } from 'vue'

defineProps({
  slots: { type: Array, required: true },
})
defineEmits(['select'])

// Most items have no icon, so a 404 is the normal case rather than an error.
// Remembering the misses stops the browser re-requesting them on every render.
const iconFailed = reactive({})

function categoryClass (slot) {
  return slot.category ? `cat-${slot.category}` : ''
}

function pendingLabel (slot) {
  if (!slot.pending_item_id) return 'will be emptied'
  return slot.pending_item_id
}
</script>
