import rules from '../../../schemas/skill_name_rules.json' with { type: 'json' }

// 与后端共用字符白名单；格式合法后仍须通过接口检查同省名称是否重复。
const pattern = new RegExp(rules.pattern, 'u')
export const skillNameHint = rules.hint

export function skillNameError(value) {
  if (typeof value !== 'string') return '场景分类必须为文本'
  const name = value.trim()
  if (!name) return '场景分类不能为空'
  const length = Array.from(name).length
  if (length < rules.min_length || length > rules.max_length) {
    return `场景分类长度须为 ${rules.min_length}～${rules.max_length} 个字符`
  }
  if (!pattern.test(name)) return rules.format_error
  if (rules.reserved_names.includes(name.toLowerCase())) {
    return `场景分类「${name}」为系统保留名称，请使用其他名称`
  }
  return ''
}
