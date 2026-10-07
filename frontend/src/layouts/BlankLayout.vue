<template>
  <!--
    A layout with no chrome, for the pages that sit outside the signed-in app.
    Quasar requires every QPage to be a deep child of a QLayout; without this
    the login screen throws "QPage needs to be a deep child of QLayout" and the
    whole app renders blank.
  -->
  <q-layout view="lHh Lpr lFf">
    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>
