<template>
  <!--
    Stale or degraded data is called out rather than guessed at. A game update
    that moves the save layout shows up here instead of as wrong numbers.
  -->
  <q-banner
    v-if="status.warnings.length"
    dense
    class="bg-warning text-dark stale-banner"
  >
    <template #avatar><q-icon name="warning" /></template>
    <div v-for="warning in status.warnings" :key="warning" class="text-caption">
      {{ warning }}
    </div>
  </q-banner>

  <q-banner v-if="status.windowInProgress" dense class="bg-info text-dark">
    <template #avatar><q-icon name="build" /></template>
    A maintenance window is running now. The server is down and edits are being
    applied.
  </q-banner>
</template>

<script setup>
import { useStatusStore } from 'stores/status'
const status = useStatusStore()
</script>
