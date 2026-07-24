import { installLingyunMalformedUrlFix } from './utils/repairLingyunUrl.js'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './assets/style.css'

installLingyunMalformedUrlFix()

const app = createApp(App)

// 全局注册 Element Plus（AutoConfig 页面使用了大量 el-* 组件）
app.use(ElementPlus)

// 全局注册 Element Plus 图标
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(createPinia())
app.use(router)
app.mount('#app')
