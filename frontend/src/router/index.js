import { createRouter, createWebHistory } from 'vue-router'
import { hideAppLoading } from 'ling-yun-methods'
import { routerHistoryBase } from '../utils/constant'
import { PageNotFound } from 'ling-yun-custom-components'

const modules = import.meta.glob('../pages/**/index.vue')
const routes = []

for (const filePath of Object.keys(modules)) {
  // 过滤掉包含 components 文件夹的路径
  if (filePath.includes('components')) {
    continue
  }
  const arrList = filePath.split('../')
  const path = arrList[1].replace('pages', '').replace('/index.vue', '')
  const arr = path.split('/')
  const name = arr[arr.length - 1]
  if (name) {
    routes.push({
      component: modules[filePath],
      name,
      path: `/${name}`,
    })
  }
}

const router = createRouter({
  /** 灵运文档：与 Vite 子应用 publicPath 一致；见 `constant.js` 中 routerHistoryBase */
  history: createWebHistory(routerHistoryBase),
  routes: [
    {
      path: '/',
      redirect: routes?.[0]?.path,
    },
    // 与 main.py 中 SPA 入口路径一致（/znhs/template 等）
    { path: '/template', redirect: '/TemplateConfig' },
    { path: '/mapping', redirect: '/MappingConfig' },
    { path: '/interface-mapper', redirect: '/InterfaceMapper' },
    // 与 main.py 灰度/运营入口 /agent_test 对齐（页面实现为 pages/TestConsole）
    { path: '/agent_test', redirect: '/TestConsole' },
    // ── AutoConfig 子模块友好路径别名 ──────────────────────────────
    // glob 路由生成规则：取文件夹最末级名称作为 path，即 /SkillManager、/Import
    { path: '/skill-config',  redirect: '/SkillManager' },
    { path: '/skill-import',  redirect: '/Import' },
    { path: '/auto-config',   redirect: '/SkillManager' },
    ...routes,
    {
      path: '/:pathMatch(.*)*',
      name: 'PageNotFound',
      component: PageNotFound,
    },
  ],
})

router.afterEach(() => {
  hideAppLoading()
})

export default router
