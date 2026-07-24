/**
 * 全国 31 个省级行政区（不含港澳台）单一真源。
 *
 * code 采用与后端 skills-runtime 目录一致的小写拼音（如 beijing / guangdong），
 * 供创建/编辑 Skill 时的省份下拉使用：只能选择，不能手动输入。
 *
 * 顺序：4 直辖市 → 22 省 → 5 自治区。
 */
export const PROVINCES = [
  // 直辖市
  { code: 'beijing',      name: '北京' },
  { code: 'tianjin',      name: '天津' },
  { code: 'shanghai',     name: '上海' },
  { code: 'chongqing',    name: '重庆' },
  // 省
  { code: 'hebei',        name: '河北' },
  { code: 'shanxi',       name: '山西' },
  { code: 'liaoning',     name: '辽宁' },
  { code: 'jilin',        name: '吉林' },
  { code: 'heilongjiang', name: '黑龙江' },
  { code: 'jiangsu',      name: '江苏' },
  { code: 'zhejiang',     name: '浙江' },
  { code: 'anhui',        name: '安徽' },
  { code: 'fujian',       name: '福建' },
  { code: 'jiangxi',      name: '江西' },
  { code: 'shandong',     name: '山东' },
  { code: 'henan',        name: '河南' },
  { code: 'hubei',        name: '湖北' },
  { code: 'hunan',        name: '湖南' },
  { code: 'guangdong',    name: '广东' },
  { code: 'hainan',       name: '海南' },
  { code: 'sichuan',      name: '四川' },
  { code: 'guizhou',      name: '贵州' },
  { code: 'yunnan',       name: '云南' },
  { code: 'shaanxi',      name: '陕西' },
  { code: 'gansu',        name: '甘肃' },
  { code: 'qinghai',      name: '青海' },
  // 自治区
  { code: 'neimenggu',    name: '内蒙古' },
  { code: 'guangxi',      name: '广西' },
  { code: 'xizang',       name: '西藏' },
  { code: 'ningxia',      name: '宁夏' },
  { code: 'xinjiang',     name: '新疆' },
]

/** code → 中文名 映射，便于回填省份中文名 */
export const PROVINCE_NAME_MAP = PROVINCES.reduce((acc, p) => {
  acc[p.code] = p.name
  return acc
}, {})

/** 根据 code 取中文名，找不到时回退 code 本身 */
export function provinceNameOf(code) {
  return PROVINCE_NAME_MAP[code] || code || ''
}
