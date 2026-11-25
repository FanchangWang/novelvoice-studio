import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/library',
    },
    {
      path: '/library',
      name: 'library',
      component: () => import('@/views/LibraryView.vue'),
    },
    {
      path: '/books/:bookId',
      name: 'workspace',
      component: () => import('@/views/WorkspaceView.vue'),
      props: true,
    },
    {
      path: '/voices',
      name: 'voices',
      component: () => import('@/views/VoicesView.vue'),
    },
    {
      path: '/jobs',
      name: 'jobs',
      component: () => import('@/components/PhasePlaceholder.vue'),
      props: {
        eyebrow: 'JOBS / 任务队列',
        title: '批量生成任务队列',
        stage: '阶段 4',
        description:
          '阶段 3 支持逐段生成与版本回退；整章批量、失败重试与断点续跑会在任务队列中实现。',
        bullets: [
          '按章节与角色批量生成语音片段',
          '单 GPU 串行调度、失败重试与断点续跑',
          '实时查看进度、耗时与错误原因',
        ],
      },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/library',
    },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})

export default router