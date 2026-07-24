/**
 * 运营端权限 Store
 * 从 /api/user/me 获取当前登录用户的省份、角色信息，供页面权限控制使用。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiUrl, apiFetch } from '@/utils/apiUrl'

export const useAuthStore = defineStore('znhs-auth', () => {
  const account = ref('')    // 账号（灵运 userInfo.id）
  const username = ref('admin')  // 姓名/用户名（灵运 userInfo.username）
  const phone = ref('')
  const deptName = ref('')
  const province = ref('')   // province code，如 "beijing"；本部为空字符串
  const isHQ = ref(true)     // 本部用户拥有全部省份权限
  const roles = ref([])
  const loaded = ref(false)
  const loading = ref(false)

  // 角色名列表（去空去重），供页面展示
  const roleNames = computed(() =>
    [...new Set((roles.value || []).map(r => r?.roleName).filter(Boolean))]
  )

  async function fetchMe() {
    if (loaded.value || loading.value) return
    loading.value = true
    try {
      const res = await apiFetch('/api/user/me')
      if (res.status === 401) {
        // 未鉴权：保持默认值（isHQ=true），不阻塞页面
        loaded.value = true
        return
      }
      const json = await res.json()
      if (json.code === 200 && json.data) {
        const d = json.data
        account.value = d.id || ''
        username.value = d.username || 'admin'
        phone.value = d.phone || ''
        deptName.value = d.deptName || ''
        province.value = d.province || ''
        isHQ.value = d.isHQ !== false  // 默认 true（鉴权未启用时后端返回 isHQ=true）
        roles.value = d.roles || []
      }
    } catch (e) {
      console.warn('[authStore] fetchMe 失败，使用默认权限（本部）:', e)
    } finally {
      loading.value = false
      loaded.value = true
    }
  }

  /**
   * 判断当前用户是否有权对目标省份执行写操作（增/删/改）。
   * 本部用户或同省份用户返回 true；跨省份返回 false。
   * @param {string} targetProvince - 目标数据的 province code
   */
  function canWrite(targetProvince) {
    if (isHQ.value) return true
    if (!province.value) return true  // 省份未知时放行（兜底）
    return province.value === targetProvince
  }

  return {
    account, username, phone, deptName, province, isHQ, roles, roleNames,
    loaded, loading, fetchMe, canWrite,
  }
})
