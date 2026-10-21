/** 将原始标识逐段编码；历史名称中的 #、?、% 等不能改变请求地址结构。 */
export function encodePathSegments(...segments) {
  // 调用方传原始名称，由这里编码一次；保留旧名称中原本存在的 %20 等字面文本。
  return segments.map(segment => encodeURIComponent(String(segment))).join('/')
}
