<template>
  <q-page class="flex flex-center q-pa-md">
    <q-card flat bordered style="width: 100%; max-width: 380px">
      <q-card-section>
        <div class="text-h6">paleditor</div>
        <div class="text-caption text-grey-6">
          Chest browser for the Palworld server
        </div>
      </q-card-section>

      <q-form @submit="submit">
        <q-card-section class="q-pt-none">
          <q-input
            v-model="password"
            type="password"
            label="Password"
            autofocus
            outlined
            :error="Boolean(error)"
            :error-message="error"
            autocomplete="current-password"
          />
          <div class="text-caption text-grey-6 q-mt-sm">
            The owner password unlocks running the maintenance window.
          </div>
        </q-card-section>

        <q-card-actions>
          <q-btn
            type="submit"
            color="primary"
            label="Sign in"
            class="full-width"
            :loading="busy"
            :disable="!password"
          />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-page>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from 'stores/session'

const password = ref('')
const error = ref('')
const busy = ref(false)
const session = useSessionStore()
const router = useRouter()
const route = useRoute()

async function submit () {
  busy.value = true
  error.value = ''
  try {
    await session.login(password.value)
    router.push(route.query.next || { name: 'bases' })
  } catch (err) {
    error.value = err.response?.status === 401
      ? 'Wrong password'
      : 'Could not sign in'
  } finally {
    busy.value = false
  }
}
</script>
