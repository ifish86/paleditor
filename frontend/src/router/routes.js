const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('pages/LoginPage.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', name: 'bases', component: () => import('pages/BasesPage.vue') },
      {
        path: 'bases/:baseGuid',
        name: 'chests',
        component: () => import('pages/ChestListPage.vue'),
      },
      {
        path: 'chests/:containerGuid',
        name: 'chest',
        component: () => import('pages/ChestDetailPage.vue'),
      },
      { path: 'queue', name: 'queue', component: () => import('pages/QueuePage.vue') },
      { path: 'search', name: 'search', component: () => import('pages/SearchPage.vue') },
    ],
  },
  {
    path: '/:catchAll(.*)*',
    component: () => import('pages/ErrorNotFound.vue'),
    meta: { public: true },
  },
]

export default routes
