/**
 * 1) 畸形网关 URL：无协议的 host:port/ 被当作相对路径拼在网关 path 后。
 * 2) 内网绝对地址：组件库直接请求 http(s)://aifly.cmos.cmcc:8010/... ，在办公网/本机 DNS 无法解析该域名时出现 ERR_NAME_NOT_RESOLVED。
 *    通过 VITE_REWRITE_GATEWAY_ORIGINS 将此类地址改为「当前页 origin + VITE_API_URL_PREFIX + 原 path」，走灵运主站同源反代。
 */

export function repairMalformedGatewayUrl(url) {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return url
  try {
    const u = new URL(url)
    const re = /\/((?:[a-z0-9-]+\.)+[a-z]{2,}:\d{2,5})\//i
    const m = re.exec(u.pathname)
    if (!m) return url
    const inner = u.pathname.slice(m.index + 1)
    const slash = inner.indexOf('/')
    if (slash <= 0) return url
    const hostPort = inner.slice(0, slash)
    const rest = inner.slice(slash)
    if (!/^(?:[a-z0-9-]+\.)+[a-z]{2,}:\d+$/i.test(hostPort)) return url
    return `${u.protocol}//${hostPort}${rest}${u.search}${u.hash}`
  } catch {
    return url
  }
}

/**
 * 将配置的灵运网关绝对 origin 改写为同源 + API 前缀，便于主应用/nginx 反代到内网。
 * @param {string} url
 */
export function rewriteGatewayToSameOrigin(url) {
  if (typeof url !== 'string' || typeof window === 'undefined' || !/^https?:\/\//i.test(url)) {
    return url
  }
  const raw = import.meta.env.VITE_REWRITE_GATEWAY_ORIGINS
  if (raw == null || String(raw).trim() === '') return url

  const prefixes = String(raw)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)

  for (const p of prefixes) {
    if (!url.startsWith(p)) continue
    let rest = url.slice(p.length)
    if (!rest.startsWith('/')) rest = `/${rest}`

    const pathPrefix = String(import.meta.env.VITE_REWRITE_SAME_ORIGIN_PATH_PREFIX || '')
      .trim()
      .replace(/\/+$/, '')

    const apiPrefix = String(import.meta.env.VITE_API_URL_PREFIX || '')
      .trim()
      .replace(/\/+$/, '')

    if (apiPrefix && (rest === apiPrefix || rest.startsWith(`${apiPrefix}/`))) {
      return `${window.location.origin}${pathPrefix}${rest}`
    }
    return `${window.location.origin}${pathPrefix}${apiPrefix}${rest}`
  }
  return url
}

export function fixLingyunResourceUrl(url) {
  if (typeof url !== 'string') return url
  let u = rewriteGatewayToSameOrigin(url)
  u = repairMalformedGatewayUrl(u)
  return u
}

export function installLingyunMalformedUrlFix() {
  if (typeof window === 'undefined' || window.__ZNHS_GATEWAY_URL_FIX__) return
  window.__ZNHS_GATEWAY_URL_FIX__ = true

  const origFetch = window.fetch.bind(window)
  window.fetch = (input, init) => {
    if (typeof input === 'string') {
      input = fixLingyunResourceUrl(input)
    } else if (input instanceof Request) {
      const fixed = fixLingyunResourceUrl(input.url)
      if (fixed !== input.url) input = new Request(fixed, input)
    }
    return origFetch(input, init)
  }

  const origXhrOpen = XMLHttpRequest.prototype.open
  XMLHttpRequest.prototype.open = function (method, url, async, user, password) {
    const fixed = typeof url === 'string' ? fixLingyunResourceUrl(url) : url
    return origXhrOpen.call(this, method, fixed, async, user, password)
  }

  const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src')
  if (desc?.set) {
    const origSet = desc.set
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set(v) {
        const s = typeof v === 'string' ? fixLingyunResourceUrl(v) : v
        origSet.call(this, s)
      },
    })
  }
}
