<template>
  <q-layout view="hHh lpR fFf">
    <q-header elevated>
      <q-toolbar>
        <q-btn
          v-if="!isRoot"
          flat dense round icon="arrow_back"
          aria-label="Back"
          @click="$router.back()"
        />
        <q-toolbar-title class="text-weight-medium">paleditor</q-toolbar-title>

        <q-btn flat dense round icon="search" :to="{ name: 'search' }" aria-label="Search" />
        <q-btn flat dense round icon="list_alt" :to="{ name: 'queue' }" aria-label="Queue">
          <q-badge v-if="status.queuedCount" color="warning" text-color="dark" floating>
            {{ status.queuedCount }}
          </q-badge>
        </q-btn>
        <q-btn flat dense round icon="more_vert" aria-label="Menu">
          <q-menu>
            <q-list style="min-width: 180px">
              <q-item-label header class="text-caption">
                Signed in as {{ session.role }}
              </q-item-label>
              <q-item clickable v-close-popup @click="refresh">
                <q-item-section avatar><q-icon name="refresh" /></q-item-section>
                <q-item-section>Refresh status</q-item-section>
              </q-item>
              <q-separator />
              <q-item clickable v-close-popup @click="signOut">
                <q-item-section avatar><q-icon name="logout" /></q-item-section>
                <q-item-section>Sign out</q-item-section>
              </q-item>
            </q-list>
          </q-menu>
        </q-btn>
      </q-toolbar>

      <StatusBanner />
    </q-header>

    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from 'stores/session'
import { useStatusStore } from 'stores/status'
import { useCatalogStore } from 'stores/catalog'
import StatusBanner from 'components/StatusBanner.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const status = useStatusStore()
const catalog = useCatalogStore()

const isRoot = computed(() => route.name === 'bases')

onMounted(() => {
  status.load()
  catalog.load()
})

function refresh () {
  status.load()
  catalog.load(true)
}

async function signOut () {
  await session.logout()
  router.push({ name: 'login' })
}
</script>
