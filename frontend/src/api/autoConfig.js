/**
 * AutoConfig 子模块 API 封装
 *
 * 使用 znhs 统一 http 实例（携带 satoken、withCredentials、baseURL 环境前缀）。
 * 所有路径统一加 /api/auto-config 前缀，由主服务 main.py 提供（同端口 :8000）。
 *
 * dev/gray:  baseURL=/znhs-gray  →  /znhs-gray/api/auto-config/... → proxy → :8000
 * prod:      baseURL=/znhs       →  /znhs/api/auto-config/...      → 网关路由 → :8000
 */
import http from '@/api/index.js'
import { apiUrl } from '@/utils/apiUrl'

const P = '/api/auto-config'

// ── 省份 / 模板 ─────────────────────────────────────────────────
export const acGetProvinces       = ()    => http.get(`${P}/provinces`)
export const acGetExampleTemplate = ()    => http.get(`${P}/template/example`)
export const acGetExcelDoc        = ()    => http.get(`${P}/template/excel-doc`)
export const acGetTemplateLibrary = ()    => http.get(`${P}/templates/library`)
export const acGetTemplateDetail  = (id)  => http.get(`${P}/templates/library/${id}`)

/** Excel 模板下载直链（带环境前缀） */
export const acExcelTemplateUrl = () => apiUrl(`${P}/template/excel`)

// ── 导入流程 ────────────────────────────────────────────────────
export const acParseJsonText   = (data)     => http.post(`${P}/import/parse-text`, data)
export const acParseExcel      = (formData) =>
  http.post(`${P}/import/excel`, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
export const acGeneratePreview = (template) => http.post(`${P}/import/generate`, { template })
export const acValidateImport  = (template, opts = {}) =>
  http.post(`${P}/import/validate`, { template, ...opts })
export const acPublishSkill    = (payload)  => http.post(`${P}/import/publish`, payload)

// ── Skill 管理 ──────────────────────────────────────────────────
export const acListSkills = (params = {}) => http.get(`${P}/skills`, { params })
/** 新建 skill 重名前置校验：以运行时 registry（ES/Redis 生效配置）为准判断省份下场景分类是否已存在 */
export const acSkillExists = (province, intent) =>
  http.get(`${P}/skills/exists`, { params: { province, intent } })
/** 一键导出全部技能包配置（返回 JSON blob，用于前端触发下载） */
export const acExportAllSkills = (params = {}) =>
  http.get(`${P}/skills/export`, { params, responseType: 'blob' })
/** 批量导入技能包配置（接口+话术模板），直接回传导出的 JSON。dry_run=true 仅预检 */
export const acImportAllSkills = (payload) =>
  http.post(`${P}/skills/import`, payload)
export const acSkillAsTemplate = (province, intent) =>
  http.get(`${P}/skills/${province}/${intent}/as-template`)
export const acGetSkillConfig = (province, intent) =>
  http.get(`${P}/skills/${province}/${intent}/config`)
/** 只读审计 Context 工程链路（接口→固定域→模板槽位） */
export const acContextAudit = (province, intent) =>
  http.get(`${P}/skills/${province}/${intent}/context-audit`)
export const acUpdateSkillConfig = (province, intent, config_type, content) =>
  http.put(`${P}/skills/${province}/${intent}/config/${config_type}`, { config_type, content })
export const acDeleteSkill = (province, intent) =>
  http.delete(`${P}/skills/${province}/${intent}`)
export const acReloadSkill = (province, intent) =>
  http.post(`${P}/skills/${province}/${intent}/reload`)
export const acUpdateSkillStatus = (province, intent, status) =>
  http.patch(`${P}/skills/${province}/${intent}/status`, { status })
