import { store } from 'quasar/wrappers'
import { createPinia } from 'pinia'

// Quasar picks this up automatically and installs Pinia for the app. Nothing
// here needs a state library beyond Pinia for the session and item catalogue.
export default store(() => createPinia())
