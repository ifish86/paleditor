/* eslint-env node */
// CommonJS: @quasar/app-vite v1 requires this file with Node directly, and the
// package is not declared as an ES module. Everything under src/ is ESM, which
// Vite handles.
const { configure } = require('quasar/wrappers')

module.exports = configure(function () {
  return {
    boot: ['api'],
    css: ['app.scss'],
    extras: ['roboto-font', 'material-icons'],

    build: {
      target: { browser: ['es2022', 'firefox115', 'chrome115', 'safari15'] },
      vueRouterMode: 'history',
      // The FastAPI app serves dist/spa directly, so one systemd unit covers
      // both the API and the UI.
      distDir: 'dist/spa',
    },

    devServer: {
      open: false,
      port: Number(process.env.PALEDITOR_DEV_PORT) || 9000,
      // Loopback only. Vite binds every interface by default, which on a
      // public VPS puts the dev server - and through the proxy below, the
      // whole API including the owner-only endpoints - on the public internet.
      // That would quietly undo the config loader's refusal to bind a public
      // address without allow_public.
      //
      // This is also what editor port forwarding wants: it forwards the remote
      // machine's localhost. To expose it deliberately, set PALEDITOR_DEV_HOST
      // to an address you mean, such as a VPN one.
      host: process.env.PALEDITOR_DEV_HOST || '127.0.0.1',
      proxy: {
        // In development the API runs separately; in production it is the same
        // origin, so no proxy is needed there.
        '/api': {
          target: process.env.PALEDITOR_API || 'http://127.0.0.1:8080',
          changeOrigin: true,
        },
      },
    },

    framework: {
      config: { dark: 'auto' },
      plugins: ['Notify', 'Dialog', 'LoadingBar'],
    },

    animations: [],
  }
})
