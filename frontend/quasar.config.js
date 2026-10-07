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
      port: 9000,
      proxy: {
        // In development the API runs separately; in production it is the same
        // origin, so no proxy is needed there.
        '/api': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      },
    },

    framework: {
      config: { dark: 'auto' },
      plugins: ['Notify', 'Dialog', 'LoadingBar'],
    },

    animations: [],
  }
})
