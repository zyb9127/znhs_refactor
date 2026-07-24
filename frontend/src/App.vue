<template>
  <!-- 灵运鉴权：等待用户信息加载后再渲染页面内容，防止未鉴权用户看到数据 -->
  <ElConfigProvider :locale="zhCn" v-if="userStore?.userInfoData?.userInfo?.id || authReady">
    <div class="znhs-app w-full relative" ref="appRootRef">
      <RouterView v-slot="{ Component }">
        <KeepAlive
          :exclude="excludedComponents"
          :include="includedComponents"
          :max="keepAliveMap?.max"
        >
          <component :is="Component" :key="getComponentKey($route?.name)" />
        </KeepAlive>
      </RouterView>
    </div>
  </ElConfigProvider>
  <div v-else class="znhs-auth-loading">
    <div class="znhs-auth-loading-inner">
      <div class="znhs-auth-spinner"></div>
      <div>鉴权中，请稍候…</div>
    </div>
  </div>
</template>

<script setup>
import { RouterView } from 'vue-router';
import { onMounted, onUnmounted, ref, nextTick, watch } from 'vue';
import { useUserStore } from '@/stores/user';
import { useAuthStore } from '@/stores/authStore';
import { keepAliveMap, useKeepAlive } from 'ling-yun-methods';
import { ElConfigProvider } from 'element-plus';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import { systemEnvironment } from './utils/constant';
import { useWujieRouter } from './hooks/useWujieRouter';

const userStore = useUserStore();
const authStore = useAuthStore();
const { vueRoute } = useWujieRouter();

// 鉴权就绪标志：getUserInfo 完成后置 true，防止无限 loading
const authReady = ref(false);

// ── 嵌入宿主（灵运/Wujie）时的滚动高度自适应 ───────────────
// CSS 的 height:100% 会解析到「整个窗口」而非宿主内容区，导致根容器比可视区更高、
// 底部内容落在折叠线以下无法滚动到。这里按「视口高度 - 根容器距视口顶部的距离」
// 精确计算可用高度，使内部 overflow:auto 能滚动到最后一行。
const appRootRef = ref(null);
let _adjustRaf = 0;
let _rootResizeObserver = null;

function adjustAppHeight() {
  const el = appRootRef.value;
  if (!el) return;
  // clamp top>=0：宿主文档若已滚动导致 rect.top 为负时，避免高度超出视口再次被裁剪
  const top = Math.max(0, el.getBoundingClientRect().top);
  const avail = Math.max(120, Math.round(window.innerHeight - top));
  el.style.height = avail + 'px';
}

function scheduleAdjust() {
  cancelAnimationFrame(_adjustRaf);
  _adjustRaf = requestAnimationFrame(adjustAppHeight);
}

// 鉴权就绪后 .znhs-app 才渲染，需等 DOM 出现再测量
watch(
  () => userStore?.userInfoData?.userInfo?.id || authReady.value,
  () => nextTick(scheduleAdjust),
);

const {
  excludedComponents,
  includedComponents,
  getComponentKey,
  setupEventListeners,
  cleanupEventListeners,
} = useKeepAlive(systemEnvironment, vueRoute);

onMounted(async () => {
  try {
    await userStore.getUserInfo();
  } catch (e) {
    console.warn('[App] getUserInfo 失败:', e);
  } finally {
    // 无论 getUserInfo 成功/失败/401，都解除 loading，防止页面永久空白
    authReady.value = true;
  }
  // 同步获取运营权限信息（省份/角色），失败不阻塞页面
  authStore.fetchMe().catch(() => {});
  setupEventListeners();

  // 高度自适应：首帧 + 延迟补测（宿主布局可能晚于挂载）+ 窗口/文档尺寸变化
  await nextTick();
  scheduleAdjust();
  setTimeout(scheduleAdjust, 150);
  setTimeout(scheduleAdjust, 600);
  window.addEventListener('resize', scheduleAdjust);
  if (typeof ResizeObserver !== 'undefined') {
    _rootResizeObserver = new ResizeObserver(() => scheduleAdjust());
    _rootResizeObserver.observe(document.documentElement);
  }
});

onUnmounted(() => {
  cleanupEventListeners();
  window.removeEventListener('resize', scheduleAdjust);
  if (_rootResizeObserver) { _rootResizeObserver.disconnect(); _rootResizeObserver = null; }
  cancelAnimationFrame(_adjustRaf);
});
</script>

<style scoped>
/* 应用根容器：作为滚动容器，内容超出时出现竖向/横向滚动条。
   height 由 JS（adjustAppHeight）按「视口高度 - 距顶距离」精确设置；
   此处 100vh 仅为 JS 执行前的兜底，避免闪烁。 */
.znhs-app {
  height: 100vh;
  overflow: auto;
}
.znhs-auth-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  width: 100vw;
  background: #f5f7fa;
}
.znhs-auth-loading-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  color: #6c757d;
  font-size: 14px;
}
.znhs-auth-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #dee2e6;
  border-top-color: #3b5bdb;
  border-radius: 50%;
  animation: znhs-spin 0.8s linear infinite;
}
@keyframes znhs-spin {
  to { transform: rotate(360deg); }
}
</style>
