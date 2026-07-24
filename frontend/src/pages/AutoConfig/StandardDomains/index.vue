<template>
  <div class="std-domains-page">
    <!-- 返回上一级 -->
    <div class="sd-topbar">
      <el-button size="small" plain @click="$router.push('/SkillManager')">
        <el-icon><ArrowLeft /></el-icon>&nbsp;返回上一级
      </el-button>
    </div>

    <!-- 顶部说明 -->
    <header class="sd-head">
      <h1 class="sd-title">📖 智能话术配置用户手册</h1>
      <p class="sd-sub">
        一个智能话术配置 = <b>省份 + 业务意图</b> 的完整话术能力包。
        上线一个智能话术配置，你只需要准备两样材料：<b>接口文档</b>（含成功响应示例 JSON）和 <b>话术文案</b>（含适用环节/场景）。
        本手册按配置顺序分四章，建议先读第一章了解整体流程，再按需查阅各章细节。
      </p>
      <nav class="sd-nav">
        <a class="sd-nav-chip" href="#sd-ch1">第一章 · 配置上线整体流程</a>
        <a class="sd-nav-chip" href="#sd-ch2">第二章 · 接口配置</a>
        <a class="sd-nav-chip" href="#sd-ch3">第三章 · 数据映射</a>
        <a class="sd-nav-chip" href="#sd-ch4">第四章 · 话术模板配置</a>
      </nav>
    </header>

    <!-- ═════════ 第一章 · 配置上线整体流程 ═════════ -->
    <h2 id="sd-ch1" class="sd-h2">第一章 · 配置上线整体流程</h2>

    <h3 class="sd-h3">你需要提供什么</h3>
    <div class="sd-prepare">
      <article class="sd-prepare-card">
        <div class="sd-prepare-icon">📄</div>
        <div>
          <div class="sd-prepare-title">接口文档</div>
          <div class="sd-prepare-desc">
            数据从哪来。需包含：接口名称、URL、请求方式、请求参数、<b>成功响应示例 JSON（必须）</b>。
            接口未就绪时可先用 Mock 数据联调。→ 详见<a href="#sd-ch2">第二章</a>
          </div>
        </div>
      </article>
      <article class="sd-prepare-card">
        <div class="sd-prepare-icon">💬</div>
        <div>
          <div class="sd-prepare-title">话术文案</div>
          <div class="sd-prepare-desc">
            对客户说什么。需包含：话术内容（可含 <code>{pkg_brief}</code> 等占位符）、
            适用环节/场景、关联的产品 ID。支持 CSV 批量导入。→ 详见<a href="#sd-ch4">第四章</a>
          </div>
        </div>
      </article>
    </div>

    <h3 class="sd-h3">整体流程图示</h3>
    <div class="sd-pipeline">
      <div class="sd-stage">
        <div class="sd-stage-num">1</div>
        <div class="sd-stage-name">创建智能话术配置</div>
        <div class="sd-stage-do">填写省份代码、意图名称</div>
        <div class="sd-stage-out">产出：空白 智能话术配置包</div>
      </div>
      <div class="sd-stage-arrow">→</div>
      <div class="sd-stage">
        <div class="sd-stage-num">2</div>
        <div class="sd-stage-name">配置接口</div>
        <div class="sd-stage-do">录入 URL / 请求模板 / Mock</div>
        <div class="sd-stage-out">产出：可调用的数据源</div>
      </div>
      <div class="sd-stage-arrow">→</div>
      <div class="sd-stage">
        <div class="sd-stage-num">3</div>
        <div class="sd-stage-name">数据映射</div>
        <div class="sd-stage-do">响应字段 → 标准数据域</div>
        <div class="sd-stage-out">产出：话术可用变量</div>
      </div>
      <div class="sd-stage-arrow">→</div>
      <div class="sd-stage">
        <div class="sd-stage-num">4</div>
        <div class="sd-stage-name">配置话术模板</div>
        <div class="sd-stage-do">写话术 + 勾选关联变量</div>
        <div class="sd-stage-out">产出：话术生成规则</div>
      </div>
      <div class="sd-stage-arrow">→</div>
      <div class="sd-stage">
        <div class="sd-stage-num">5</div>
        <div class="sd-stage-name">检查与测试</div>
        <div class="sd-stage-do">数据流闭环检查 + 测试弹窗</div>
        <div class="sd-stage-out">产出：验证通过的配置</div>
      </div>
      <div class="sd-stage-arrow">→</div>
      <div class="sd-stage hl">
        <div class="sd-stage-num">6</div>
        <div class="sd-stage-name">发布上线</div>
        <div class="sd-stage-do">发布 + 热重载即时生效</div>
        <div class="sd-stage-out">产出：线上可用智能话术配置</div>
      </div>
    </div>

    <h3 class="sd-h3">各阶段操作说明</h3>
    <table class="sd-vars-table">
      <thead>
        <tr><th>阶段</th><th>在哪操作</th><th>你要做什么</th><th>关键点</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in STAGES" :key="s.num">
          <td style="white-space:nowrap;"><b>{{ s.num }}. {{ s.name }}</b></td>
          <td>{{ s.where }}</td>
          <td>{{ s.what }}</td>
          <td>{{ s.key }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="sd-h3">状态流转与版本管理</h3>
    <div class="sd-status-flow">
      <span class="sd-status-chip draft">编辑中</span>
      <span class="sd-status-arrow">─ 发布 →</span>
      <span class="sd-status-chip published">已发布</span>
      <span class="sd-status-arrow">─ 下线 →</span>
      <span class="sd-status-chip offline">已下线</span>
      <span class="sd-status-arrow">─ 删除 →</span>
      <span class="sd-status-chip deleted">已删除（保留备份）</span>
    </div>
    <ol class="sd-manual-list" style="margin-top:10px;">
      <li>已发布的智能话术配置 保存任何改动会自动转回「编辑中」，需<b>重新发布</b>才能生效</li>
      <li>每次保存配置时系统<b>自动备份</b>；点击「备份」按钮可手动备份，备份后<b>版本号自动 +1</b></li>
      <li>「历史版本」中可查看每个备份的完整配置详情，选中后一键<b>恢复</b>（恢复即时生效）</li>
      <li>已发布的智能话术配置 不可直接删除，需先<b>下线</b>，保证生产稳定</li>
    </ol>

    <!-- ═════════ 第二章 · 接口配置详细说明 ═════════ -->
    <h2 id="sd-ch2" class="sd-h2">第二章 · 接口配置详细说明</h2>
    <p class="sd-sub" style="margin-bottom:12px;">
      接口配置解决「<b>数据从哪来</b>」：把推荐引擎、用户画像等外部接口接进 智能话术配置。
      入口：<b>智能话术配置编辑 → 接口配置 Tab</b>（创建 智能话术配置页同样支持）。
    </p>

    <h3 class="sd-h3">需要提供的材料：接口文档要素清单</h3>
    <table class="sd-vars-table">
      <thead>
        <tr><th>要素</th><th>是否必须</th><th>说明 / 示例</th></tr>
      </thead>
      <tbody>
        <tr v-for="f in IFC_DOC_ITEMS" :key="f.name">
          <td style="white-space:nowrap;"><b>{{ f.name }}</b></td>
          <td style="white-space:nowrap;">{{ f.required }}</td>
          <td>{{ f.desc }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="sd-h3">配置字段说明</h3>
    <table class="sd-vars-table">
      <thead>
        <tr><th>字段</th><th>名称</th><th>说明 / 示例</th></tr>
      </thead>
      <tbody>
        <tr v-for="f in IFC_FIELDS" :key="f.key">
          <td><code>{{ f.key }}</code></td>
          <td style="white-space:nowrap;">{{ f.label }}</td>
          <td>{{ f.desc }}</td>
        </tr>
      </tbody>
    </table>

    <div class="sd-manual">
      <article class="sd-manual-card" style="grid-column: 1 / -1;">
        <header class="sd-manual-hd">
          <span class="sd-manual-num">A</span>三种配置方式（任选其一）
          <span style="margin-left:auto; display:flex; gap:8px;">
            <el-button size="small" type="primary" plain @click="downloadIfcSample">
              ⬇ 下载接口配置 JSON 模板
            </el-button>
            <el-button size="small" plain @click="downloadIfcDocSample">
              ⬇ 下载智能解析文本样例
            </el-button>
          </span>
        </header>
        <ol class="sd-manual-list">
          <li><b>方式一 · 上传 .docx 自动解析（推荐）</b>：智能话术配置编辑 → 接口配置 → 新建接口 → 上传文档自动解析。
            文档需包含上方要素清单的全部内容，仅支持 .docx 格式</li>
          <li><b>方式二 · 粘贴文档智能解析</b>：创建 智能话术配置页 → 接口配置 → 智能映射面板，
            粘贴接口文档文本（Markdown / 纯文本 / curl 示例均可），AI 自动生成接口节点</li>
          <li><b>方式三 · 手动填写</b>：「新建接口」三步弹窗逐项填入（① 基础信息 → ② 请求配置 → ③ 出参映射），
            可对照下方 JSON 模板理解每个字段</li>
        </ol>
        <details class="sd-example" open>
          <summary>接口配置 JSON 模板（含出参映射，可下载）</summary>
          <pre class="sd-pre">{{ IFC_JSON_SAMPLE }}</pre>
        </details>
        <details class="sd-example">
          <summary>智能解析粘贴文本样例（可下载）</summary>
          <pre class="sd-pre">{{ IFC_DOC_SAMPLE }}</pre>
        </details>
      </article>

      <article class="sd-manual-card" style="grid-column: 1 / -1;">
        <header class="sd-manual-hd"><span class="sd-manual-num">B</span>Mock 模式：接口未就绪也能配置</header>
        <ol class="sd-manual-list">
          <li>真实接口还没开发好？编辑接口时开启 <b>Mock 模式</b>，录入一份模拟响应 JSON</li>
          <li>Mock 数据的结构应与真实接口一致，这样后续切换真实接口时<b>出参映射无需改动</b></li>
          <li>用 Mock 数据即可完成出参映射、话术联调、测试全流程</li>
          <li>接口就绪后关闭 Mock 模式并填入真实 URL 即可</li>
        </ol>
      </article>
    </div>

    <!-- ═════════ 第三章 · 数据映射详细说明 ═════════ -->
    <h2 id="sd-ch3" class="sd-h2">第三章 · 数据映射详细说明</h2>
    <p class="sd-sub" style="margin-bottom:12px;">
      数据映射解决「<b>接口数据怎么变成话术变量</b>」。各省接口返回的字段名千差万别，
      话术模板要跨省份/跨接口复用，就必须把接口字段「对号入座」填进统一的<b>标准数据域 <code>resource_context</code></b>（7 大固定域 + 1 个扩展域）。
      映射完成后，话术模板里的 <code>{pkg_brief}</code>、<code>{usage}</code> 等变量才有真实值。
      入口：<b>编辑接口 → ③ 出参映射</b>。
    </p>

    <h3 class="sd-h3">映射逻辑：两步取数</h3>
    <ol class="sd-manual-list" style="margin-bottom:14px;">
      <li><b>第一步 响应提取 <code>response_extract</code></b>：按 JSON 路径从接口响应里整块取数。
        例如 <code>"current_package": "bean.mainoffer"</code> 表示把响应里 <code>bean.mainoffer</code> 整块放进「当前套餐」域</li>
      <li><b>第二步 字段加工 <code>field_transform</code></b>（可选）：对取出的数据做字段筛选（保留/排除）、单位换算（MB→GB、分→元）等再写入标准域</li>
      <li>推荐用 <b>智能自动映射</b>：粘贴一份响应样例 JSON → 智能分析 → 应用保存，系统自动生成上述两步配置</li>
      <li>「真实数据流」区域可实时预览每个标准域的最终取值，所见即所得</li>
    </ol>

    <!-- 术语速查 -->
    <details class="sd-glossary" open>
      <summary>术语速查</summary>
      <dl class="sd-terms">
        <div class="sd-term"><dt>响应提取 <code>response_extract</code></dt><dd>按 JSON 路径整块取数。例 <code>"current_package": "bean.mainoffer"</code>。</dd></div>
        <div class="sd-term"><dt>字段保留 <code>filter_include</code></dt><dd>从中间数据集只保留指定字段，其它丢弃。</dd></div>
        <div class="sd-term"><dt>字段排除 <code>filter_exclude</code></dt><dd>从中间数据集去掉指定字段，剩余全部保留。</dd></div>
        <div class="sd-term"><dt>直接透传 <code>passthrough</code></dt><dd>整块原样写入标准域。</dd></div>
        <div class="sd-term"><dt>单位转换 <code>unit_convert</code></dt><dd>写入时换算单位，如 MB→GB、分→元。带 ⚖ 标记。</dd></div>
        <div class="sd-term"><dt>JSON 路径</dt><dd>点号分隔层级。例 <code>bean.mainoffer</code> 指向 <code>{"bean":{"mainoffer":...}}</code>。</dd></div>
      </dl>
    </details>

    <!-- 7 大标准域 -->
    <h3 class="sd-h3">7 大固定域 + 1 个扩展域</h3>
    <div class="sd-grid">
      <article v-for="d in DOMAINS" :key="d.key" class="sd-card" :class="{ ext: d.ext }">
        <header class="sd-card-head">
          <span class="sd-card-title">{{ d.label }}</span>
          <code class="sd-card-key">{{ d.key }}</code>
          <span v-if="d.ext" class="sd-card-flag">扩展</span>
        </header>
        <p class="sd-card-desc">{{ d.desc }}</p>

        <div class="sd-row">
          <span class="sd-row-label">典型来源</span>
          <span class="sd-row-val">
            <code v-for="f in d.sourceFields" :key="f" class="sd-chip">{{ f }}</code>
          </span>
        </div>
        <div class="sd-row">
          <span class="sd-row-label">话术变量</span>
          <span class="sd-row-val">
            <code v-for="v in d.vars" :key="v" class="sd-chip sd-chip-var">{{ v }}</code>
            <span v-if="!d.vars.length" class="sd-empty">无内置变量</span>
          </span>
        </div>

        <details class="sd-example">
          <summary>映射示例</summary>
          <pre class="sd-pre">{{ d.example }}</pre>
        </details>
      </article>
    </div>

    <!-- 完整流程图示 -->
    <h3 class="sd-h3">完整数据流样例：从接口响应到标准域</h3>
    <div class="sd-demo">
      <section class="sd-demo-col">
        <div class="sd-demo-label">① 接口响应 JSON（接口返回的原始数据）</div>
        <pre class="sd-pre">{{ DEMO_RESPONSE }}</pre>
      </section>
      <div class="sd-demo-arrow">→</div>
      <section class="sd-demo-col">
        <div class="sd-demo-label">② 映射配置（提取 + 加工规则）</div>
        <pre class="sd-pre">{{ DEMO_TRANSFORM }}</pre>
      </section>
      <div class="sd-demo-arrow">→</div>
      <section class="sd-demo-col">
        <div class="sd-demo-label">③ 标准域结果（话术变量取值来源）</div>
        <pre class="sd-pre">{{ DEMO_RESULT }}</pre>
      </section>
    </div>

    <h3 class="sd-h3">数据流闭环检查</h3>
    <ol class="sd-manual-list">
      <li>「数据流映射」Tab 可视化展示 <b>接口产出 ↔ 话术消费</b> 的对应关系</li>
      <li>左侧按接口分组展示产出的标准数据域及字段；右侧话术模板分「已关联 / 未关联」两组</li>
      <li>出现红色「<b>变量未闭环</b>」提示，说明话术用到的变量没有接口为它供数——返回出参映射补配对应域即可</li>
    </ol>

    <!-- ═════════ 第四章 · 话术模板配置说明 ═════════ -->
    <h2 id="sd-ch4" class="sd-h2">第四章 · 话术模板配置说明</h2>
    <p class="sd-sub" style="margin-bottom:12px;">
      话术模板解决「<b>对客户说什么</b>」：固定文案 + <code>{变量}</code> 占位符，由大模型结合真实数据生成最终话术。
      入口：<b>智能话术配置编辑 → 话术模板 Tab → 添加模板</b>。
    </p>

    <h3 class="sd-h3">模板字段说明</h3>
    <table class="sd-vars-table">
      <thead>
        <tr><th>字段</th><th>是否必须</th><th>说明 / 示例</th></tr>
      </thead>
      <tbody>
        <tr v-for="f in TPL_FIELDS" :key="f.name">
          <td style="white-space:nowrap;"><b>{{ f.name }}</b></td>
          <td style="white-space:nowrap;">{{ f.required }}</td>
          <td>{{ f.desc }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="sd-h3">占位符与关联变量的配套规则</h3>
    <p class="sd-tip" style="margin:0 0 14px;">
      ① 话术内容里写了 <code>{pkg_brief}</code> 等占位符，就必须在「关联变量」中勾选同名变量，否则生成时变量为空
      （系统检测到占位符会自动推荐勾选）；
      ② 只勾选不写占位符也可以——变量值会作为上下文提供给大模型参考，由大模型自行组织进话术；
      ③ <code>table</code> 比较特殊，仅用于前端展示差异表格，不会打入大模型 Prompt。
    </p>

    <h3 class="sd-h3">可用变量一览 · 接口数据域变量（由第三章的数据映射写入，需配置接口才有值）</h3>
    <table class="sd-vars-table">
      <thead>
        <tr><th>变量名</th><th>名称</th><th>数据来源</th><th>内容说明 / 示例</th></tr>
      </thead>
      <tbody>
        <tr v-for="v in DOMAIN_VARS" :key="v.key">
          <td><code>{{ '{' + v.key + '}' }}</code></td>
          <td>{{ v.label }}</td>
          <td>{{ v.source }}</td>
          <td>{{ v.desc }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="sd-h3">可用变量一览 · 计算生成变量（话术生成时由系统自动计算，无需接口配置）</h3>
    <table class="sd-vars-table">
      <thead>
        <tr><th>变量名</th><th>名称</th><th>数据来源</th><th>内容说明 / 示例</th></tr>
      </thead>
      <tbody>
        <tr v-for="v in GENERATED_VARS" :key="v.key">
          <td><code>{{ '{' + v.key + '}' }}</code></td>
          <td>{{ v.label }}</td>
          <td>{{ v.source }}</td>
          <td>{{ v.desc }}</td>
        </tr>
      </tbody>
    </table>

    <div class="sd-manual" style="margin-top:14px;">
      <article class="sd-manual-card" style="grid-column: 1 / -1;">
        <header class="sd-manual-hd">
          <span class="sd-manual-num">A</span>CSV 批量导入（导入文件模板）
          <el-button size="small" type="primary" plain style="margin-left:auto;" @click="downloadTplCsv">
            ⬇ 下载话术模板 CSV 样例
          </el-button>
        </header>
        <ol class="sd-manual-list">
          <li>入口：<b>智能话术配置编辑 → 话术模板 Tab → 导入 CSV</b>（创建 智能话术配置页同样支持）</li>
          <li>列顺序（首行为表头）：<code>模板名称, 应用环节, 应用场景, 产品ID, 话术内容, 关联变量, 状态</code></li>
          <li>关联变量多个时用 <code>|</code> 分隔（如 <code>pkg_brief|usage</code>）；状态填 <code>online</code> 或 <code>offline</code></li>
          <li>话术内容含逗号时需用英文双引号包裹整个字段</li>
          <li>导入后<b>追加</b>到当前模板列表末尾，不会覆盖已有模板</li>
        </ol>
        <details class="sd-example" open>
          <summary>CSV 内容样例（可下载）</summary>
          <pre class="sd-pre">{{ TPL_CSV_SAMPLE }}</pre>
        </details>
      </article>
    </div>

    <p class="sd-tip">
      配置完成后，回到<b>第一章流程的第 5、6 步</b>：在「数据流映射」确认变量闭环 → 「测试」弹窗填手机号验证话术效果 → 「发布」上线。
    </p>
  </div>
</template>

<script setup>
// ── 第一章：整体流程各阶段说明 ─────────────────────────
const STAGES = [
  { num: 1, name: '创建智能话术配置', where: '创建 智能话术配置页（智能话术配置管理 → 导入新智能话术配置）',
    what: '填写省份代码（如 guangdong）、意图名称（如 套餐推荐）',
    key: '一个业务意图对应一个 智能话术配置包' },
  { num: 2, name: '配置接口', where: '接口配置 Tab → 新建接口',
    what: '录入 URL、请求方法、请求模板，或上传文档自动解析',
    key: '接口未就绪可先开 Mock 模式联调（详见第二章）' },
  { num: 3, name: '数据映射', where: '编辑接口 → ③ 出参映射',
    what: '把接口响应字段填入标准数据域，推荐用智能自动映射',
    key: '映射后话术变量才有真实值（详见第三章）' },
  { num: 4, name: '配置话术模板', where: '话术模板 Tab → 添加模板',
    what: '填写环节/场景/产品 ID，编写含占位符的话术并勾选关联变量',
    key: '占位符与关联变量需配套（详见第四章）' },
  { num: 5, name: '检查与测试', where: '数据流映射 Tab + 列表「测试」按钮',
    what: '确认无「变量未闭环」红色提示；测试弹窗填手机号验证话术效果',
    key: '测试通过再发布，避免线上空变量' },
  { num: 6, name: '发布上线', where: '智能话术配置管理列表「发布」按钮',
    what: '发布后可选自动热重载，配置立即生效',
    key: '后续修改会回到「编辑中」，需重新发布' },
]

// ── 第二章：接口文档要素与字段说明 ──────────────────────
const IFC_DOC_ITEMS = [
  { name: '接口名称', required: '必须', desc: '英文标识，如 marketing_recommend_api，作为接口节点的唯一名称' },
  { name: '接口描述', required: '建议', desc: '一句话说明接口用途，如「营销推荐主接口」' },
  { name: 'URL + 请求方式', required: '必须', desc: '完整地址与 GET/POST，如 POST http://10.0.0.1:8080/api/recommend' },
  { name: '请求参数说明', required: '必须', desc: '字段名、类型、含义，如 phone(string) 用户手机号' },
  { name: '成功响应示例 JSON', required: '必须', desc: '一份真实/模拟的完整响应。出参映射和智能解析全靠它，缺失则无法自动映射' },
  { name: '响应字段说明', required: '建议', desc: '关键字段的业务含义，帮助智能映射更准确' },
]

const IFC_FIELDS = [
  { key: 'api_name', label: '接口名称', desc: '英文唯一标识，如 marketing_recommend_api' },
  { key: 'description', label: '接口描述', desc: '用途说明，显示在接口列表中' },
  { key: 'url', label: '请求地址', desc: '完整 URL，如 http://host:port/api/recommend' },
  { key: 'method', label: '请求方式', desc: 'GET 或 POST' },
  { key: 'request_template', label: '请求模板', desc: '请求体 JSON，支持 {phone}、{province} 等变量，运行时自动替换为真实值' },
  { key: 'response_extract', label: '响应提取', desc: '按 JSON 路径取数写入标准域（第三章详述）' },
  { key: 'field_transform', label: '字段加工', desc: '字段筛选 / 单位换算后写入标准域（第三章详述）' },
  { key: 'mock_mode', label: 'Mock 开关', desc: 'true 时不调真实接口，直接返回 mock_response' },
  { key: 'mock_response', label: 'Mock 响应', desc: '模拟响应 JSON，结构需与真实接口一致' },
]

// ── 第四章：话术模板字段说明 ───────────────────────────
const TPL_FIELDS = [
  { name: '模板名称', required: '必须', desc: '便于识别的名称，如「营销推荐」「挽留话术」「开场白」' },
  { name: '应用环节', required: '必须', desc: '话术在通话流程中的位置，如 开场环节 / 推荐环节 / 挽留环节' },
  { name: '应用场景', required: '可选', desc: '细分触发场景，如 流量不足 / 合约到期；留空表示该环节通用' },
  { name: '产品 ID', required: '可选', desc: '关联的推荐产品编码（如 P200），同一话术可关联多个产品；留空表示不限产品' },
  { name: '话术内容', required: '必须', desc: '固定文案 + {变量} 占位符，如「为您推荐{pkg_brief}，性价比更高」' },
  { name: '关联变量', required: '按需', desc: '勾选话术用到的变量，生成时系统把变量真实值注入大模型 Prompt' },
  { name: '状态', required: '必须', desc: 'online（启用）/ offline（停用），停用的模板不参与话术生成' },
]

// ── 第四章：变量数据说明 ───────────────────────────────
const DOMAIN_VARS = [
  { key: 'current_package', label: '当前套餐信息', source: '接口出参映射',
    desc: '用户当前主套餐：名称、价格、流量、语音、宽带、权益。示例：{"offerName":"5G畅享套餐","offerPrice":99}' },
  { key: 'usage', label: '历史用量', source: '接口出参映射',
    desc: '近 3/6 月平均流量、语音、消费、饱和度。示例：{"近3月平均流量(GB)":12.2}' },
  { key: 'tags', label: '用户标签', source: '接口出参映射',
    desc: '身份/属性标签：是否老人、学生、终端品牌、入网渠道等' },
  { key: 'user_info', label: '用户基础信息', source: '接口出参映射',
    desc: '账号基础属性：用户等级、网龄、星级、开通时间' },
  { key: 'recommended_packages', label: '推荐产品列表', source: '接口出参映射',
    desc: '推荐策略产出的备选套餐清单；一般不直接勾选，由系统加工为 pkg_brief 后使用' },
  { key: 'user_profile', label: '用户画像', source: '接口出参映射',
    desc: '加工后的偏好画像：流量偏好、使用场景、语音偏好' },
  { key: 'domain_ext', label: '扩展信息', source: '接口出参映射',
    desc: '6 大域之外的业务专有信息：家庭业务、合约/活动、订购详情等' },
  { key: 'extra_info', label: '主服务补充信息', source: '调用方透传',
    desc: '推荐请求 extra_data 中透传的补充字段，如外呼场景、活动编号' },
  { key: 'extra_context', label: '模板匹配上下文', source: '系统运行时',
    desc: '模板命中时的环节/场景等上下文信息' },
]
const GENERATED_VARS = [
  { key: 'pkg_brief', label: '推荐产品信息', source: '话术生成时计算',
    desc: '当前推荐的那一款套餐的一句话摘要。示例："5G畅享Plus 129元/月 含60GB流量+1000分钟"' },
  { key: 'diff_str', label: '套餐差异', source: '话术生成时计算',
    desc: '当前套餐 vs 推荐套餐的差异描述。示例："月费+30元，流量+30GB，语音+500分钟"' },
  { key: 'table', label: '差异表格', source: '话术生成时计算',
    desc: '结构化差异对比表，仅用于前端展示，不打入大模型 Prompt' },
]

// ── 导入文件样例与模板 ─────────────────────────────────
const TPL_CSV_SAMPLE = `模板名称,应用环节,应用场景,产品ID,话术内容,关联变量,状态
营销推荐,推荐环节,流量不足,P200,"您目前使用的是{cur_brief}，结合您近期的用量情况，为您推荐{pkg_brief}，性价比更高。",pkg_brief|usage|current_package,online
挽留话术,挽留环节,,,"理解您的顾虑，{pkg_brief}首月还有专属优惠，您可以先体验一个月看看效果。",pkg_brief,online
开场白,开场环节,,,您好，我是中国电信客服专员，本次来电是为您做一次套餐使用情况的免费检测。,,online`

const IFC_JSON_SAMPLE = `{
  "api_name": "marketing_recommend_api",
  "description": "营销推荐主接口",
  "url": "http://host:port/api/recommend",
  "method": "POST",
  "request_template": {
    "phone": "{{PHONE}}",
    "province": "{{PROVINCE}}",
    "intent": "{{INTENT}}"
  },
  "response_extract": {
    "current_package":      "bean.mainoffer",
    "recommended_packages": "bean.recommend_results",
    "raw_tags":             "bean.tags"
  },
  "field_transform": {
    "usage.data_usage": {
      "from": "raw_tags",
      "type": "filter_include",
      "include_keys": ["近3月平均流量(MB)", "近6月平均流量(MB)"],
      "unit_convert": { "近3月平均流量(MB)": "mb_to_gb" }
    },
    "tags": {
      "from": "raw_tags",
      "type": "filter_exclude",
      "exclude_keys": ["近3月平均流量(MB)", "近6月平均流量(MB)"]
    }
  },
  "mock_mode": false,
  "mock_response": {
    "rtnCode": "0",
    "bean": {
      "mainoffer": { "offerName": "5G畅享套餐", "offerPrice": 99 },
      "tags": { "近3月平均流量(MB)": 12500, "是否老年人": "否" },
      "recommend_results": [
        { "offerCode": "P200", "offerName": "5G畅享Plus", "price": 129 }
      ]
    }
  }
}`

const IFC_DOC_SAMPLE = `# 营销推荐接口
接口名称：marketing_recommend_api
接口描述：根据手机号返回当前套餐、用户标签和推荐套餐列表
URL：http://10.0.0.1:8080/api/recommend
请求方式：POST

## 请求参数
| 字段 | 类型 | 说明 |
| phone | string | 用户手机号 |
| province | string | 省份代码 |

## 成功响应示例
{
  "rtnCode": "0",
  "bean": {
    "mainoffer": { "offerName": "5G畅享套餐", "offerPrice": 99 },
    "tags": { "近3月平均流量(MB)": 12500 },
    "recommend_results": [{ "offerName": "5G畅享Plus", "price": 129 }]
  }
}`

function _downloadFile(filename, content, mime) {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function downloadTplCsv() {
  // \ufeff BOM：保证 Excel 打开不乱码
  _downloadFile('话术模板导入样例.csv', '\ufeff' + TPL_CSV_SAMPLE, 'text/csv;charset=utf-8')
}

function downloadIfcSample() {
  _downloadFile('接口配置模板.json', IFC_JSON_SAMPLE, 'application/json;charset=utf-8')
}

function downloadIfcDocSample() {
  _downloadFile('接口文档智能解析样例.md', IFC_DOC_SAMPLE, 'text/markdown;charset=utf-8')
}

const DOMAINS = [
  {
    key: 'current_package', label: '当前套餐',
    desc: '用户当前订购的主套餐：价格、流量、语音、宽带、权益。',
    sourceFields: ['offerName', 'offerPrice', 'flowAmount', 'voiceAmount', 'rights'],
    vars: ['cur_brief'],
    example: `"current_package": "bean.mainoffer"
// 或
"current_package": {
  "from": "raw_main",
  "type": "filter_include",
  "include_keys": ["offerName", "offerPrice"]
}`,
  },
  {
    key: 'usage', label: '历史用量',
    desc: '近 3/6 月平均流量、语音、消费、饱和度等用量画像。',
    sourceFields: ['近3月平均流量', '近6月平均流量', '近3月饱和度', '近3月平均消费'],
    vars: ['usage_line'],
    example: `"usage.data_usage": {
  "from": "raw_tags",
  "type": "filter_include",
  "include_keys": ["近3月平均流量(MB)", "近6月平均流量(MB)"],
  "unit_convert": { "近3月平均流量(MB)": "mb_to_gb" }
}`,
  },
  {
    key: 'tags', label: '用户标签',
    desc: '用户身份/属性标签：是否老人、学生、商务、流量偏好等。',
    sourceFields: ['是否老年人', '是否学生', '终端品牌', '入网渠道'],
    vars: ['user_tags'],
    example: `"tags": {
  "from": "raw_tags",
  "type": "filter_exclude",
  "exclude_keys": ["近3月平均流量", "近6月平均流量"]
}`,
  },
  {
    key: 'user_info', label: '用户基础信息',
    desc: '账号基础属性：用户等级、网龄、终端类型、开通时间、星级。',
    sourceFields: ['userLevel', 'netAge', 'starLevel', 'openDate'],
    vars: ['user_info'],
    example: `"user_info": "bean.userInfo"`,
  },
  {
    key: 'recommended_packages', label: '推荐产品',
    desc: '推荐策略产出的备选套餐清单：价格/流量/权益/差异点。',
    sourceFields: ['offerCode', 'offerName', 'price', 'diffPoints'],
    vars: ['pkg_brief', 'diff_str'],
    example: `"recommended_packages": "bean.recommend_results"`,
  },
  {
    key: 'user_profile', label: '用户画像',
    desc: '加工后的用户偏好画像：流量偏好、使用场景、语音偏好。',
    sourceFields: ['flow_preference', 'voice_preference', 'scene_tags'],
    vars: ['user_profile'],
    example: `"user_profile": {
  "from": "raw_profile",
  "type": "passthrough"
}`,
  },
  {
    key: 'domain_ext', label: '扩展域', ext: true,
    desc: '上述 6 域无法对应的业务专有信息：家庭业务、合约/活动、订购详情等。',
    sourceFields: ['family_offer', 'contract_info', 'subscribe_history'],
    vars: ['domain_ext'],
    example: `"domain_ext.family_offer": "bean.familyOffer"`,
  },
]

const DEMO_RESPONSE = `{
  "rtnCode": "0",
  "bean": {
    "mainoffer": { "offerName": "5G畅享套餐", "offerPrice": 99 },
    "tags": {
      "近3月平均流量(MB)": 12500,
      "近6月平均流量(MB)": 11200,
      "是否老年人": "否",
      "终端品牌": "Apple"
    },
    "recommend_results": [
      { "offerCode": "P200", "offerName": "5G畅享Plus", "price": 129 }
    ]
  }
}`

const DEMO_TRANSFORM = `{
  "response_extract": {
    "raw_main":  "bean.mainoffer",
    "raw_tags":  "bean.tags",
    "current_package":      "bean.mainoffer",
    "recommended_packages": "bean.recommend_results"
  },
  "field_transform": {
    "usage.data_usage": {
      "from": "raw_tags",
      "type": "filter_include",
      "include_keys": ["近3月平均流量(MB)", "近6月平均流量(MB)"],
      "unit_convert": {
        "近3月平均流量(MB)": "mb_to_gb",
        "近6月平均流量(MB)": "mb_to_gb"
      }
    },
    "tags": {
      "from": "raw_tags",
      "type": "filter_exclude",
      "exclude_keys": ["近3月平均流量(MB)", "近6月平均流量(MB)"]
    }
  }
}`

const DEMO_RESULT = `{
  "current_package": { "offerName": "5G畅享套餐", "offerPrice": 99 },
  "usage": {
    "data_usage": {
      "近3月平均流量(GB)": 12.21,
      "近6月平均流量(GB)": 10.94
    }
  },
  "tags": { "是否老年人": "否", "终端品牌": "Apple" },
  "recommended_packages": [
    { "offerCode": "P200", "offerName": "5G畅享Plus", "price": 129 }
  ]
}`
</script>

<style scoped>
.std-domains-page {
  padding: 20px 24px; max-width: 1400px; margin: 0 auto;
  font-size: 13px; color: var(--text);
}

/* 返回上一级工具条 */
.sd-topbar { margin-bottom: 12px; }

/* 顶部 */
.sd-head { margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
.sd-title { font-size: 18px; font-weight: 700; margin: 0 0 6px; color: var(--text); }
.sd-title code {
  font-family: monospace; font-size: 14px; color: #495057;
  background: #f5f5f5; padding: 1px 6px; border-radius: 3px; font-weight: 600;
}
.sd-sub { font-size: 13px; color: var(--muted); line-height: 1.7; margin: 0 0 10px; }
.sd-sub b { color: var(--text); font-weight: 600; }
.sd-sub code {
  font-family: monospace; font-size: 12px; color: #495057;
  background: #f5f5f5; padding: 0 5px; border-radius: 3px;
}
.sd-sub a { color: var(--primary, #2563eb); text-decoration: none; }

/* 章节导航 */
.sd-nav { display: flex; gap: 8px; flex-wrap: wrap; }
.sd-nav-chip {
  padding: 4px 12px; border: 1px solid var(--border); border-radius: 4px;
  background: #fff; color: var(--text); font-size: 12px; font-weight: 500;
  text-decoration: none; transition: all .15s;
}
.sd-nav-chip:hover { border-color: var(--primary, #2563eb); color: var(--primary, #2563eb); }

/* 准备材料卡片 */
.sd-prepare {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 12px; margin-bottom: 16px;
}
.sd-prepare-card {
  display: flex; gap: 12px; align-items: flex-start;
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 16px;
}
.sd-prepare-icon { font-size: 24px; line-height: 1; flex-shrink: 0; margin-top: 2px; }
.sd-prepare-title { font-size: 14px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
.sd-prepare-desc { font-size: 12.5px; color: var(--muted); line-height: 1.7; }
.sd-prepare-desc b { color: var(--text); }
.sd-prepare-desc a { color: var(--primary, #2563eb); text-decoration: none; }
.sd-prepare-desc code {
  font-family: monospace; font-size: 11px; color: #495057;
  background: #f5f5f5; border: 1px solid #e9ecef;
  padding: 0 4px; border-radius: 3px;
}

/* 整体流程图示 */
.sd-pipeline {
  display: flex; align-items: stretch; gap: 6px;
  background: #fafafa; border: 1px solid var(--border); border-radius: 8px;
  padding: 14px; margin-bottom: 16px; overflow-x: auto;
}
.sd-stage {
  flex: 1; min-width: 132px;
  background: #fff; border: 1px solid var(--border); border-radius: 6px;
  padding: 10px 12px; position: relative;
}
.sd-stage.hl { border-color: var(--primary, #2563eb); }
.sd-stage-num {
  width: 20px; height: 20px; border-radius: 50%;
  background: var(--primary, #2563eb); color: #fff;
  font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 6px;
}
.sd-stage-name { font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
.sd-stage-do { font-size: 11.5px; color: var(--muted); line-height: 1.5; margin-bottom: 6px; }
.sd-stage-out {
  font-size: 11px; color: #1864ab; background: #f0f7ff;
  border: 1px solid #d0ebff; border-radius: 3px;
  padding: 2px 6px; display: inline-block;
}
.sd-stage-arrow {
  display: flex; align-items: center;
  color: #adb5bd; font-size: 15px; font-weight: 700; flex-shrink: 0;
}

/* 状态流转图示 */
.sd-status-flow {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  background: #fafafa; border: 1px solid var(--border); border-radius: 6px;
  padding: 12px 14px;
}
.sd-status-chip {
  padding: 3px 12px; border-radius: 12px; font-size: 12px; font-weight: 600;
  border: 1px solid var(--border); background: #fff; color: var(--muted);
}
.sd-status-chip.draft { color: #e8590c; border-color: #ffd8a8; background: #fff4e6; }
.sd-status-chip.published { color: #2b8a3e; border-color: #b2f2bb; background: #ebfbee; }
.sd-status-chip.offline { color: #495057; border-color: #dee2e6; background: #f8f9fa; }
.sd-status-chip.deleted { color: #adb5bd; border-style: dashed; }
.sd-status-arrow { color: #adb5bd; font-size: 12px; }

/* 术语速查 */
.sd-glossary {
  margin-bottom: 18px; padding: 8px 14px;
  background: #fafafa; border: 1px solid var(--border); border-radius: 6px;
}
.sd-glossary > summary {
  cursor: pointer; font-size: 13px; font-weight: 600; color: var(--text);
  padding: 4px 0; list-style: none;
}
.sd-glossary > summary::-webkit-details-marker { display: none; }
.sd-glossary > summary::before {
  content: '▸'; display: inline-block; width: 14px;
  color: #adb5bd; transition: transform .15s;
}
.sd-glossary[open] > summary::before { transform: rotate(90deg); }
.sd-terms {
  margin: 8px 0 0; padding-top: 8px;
  border-top: 1px dashed var(--border);
  display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 8px 18px;
}
.sd-term { padding: 4px 0; }
.sd-term dt { font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 2px; }
.sd-term dt code,
.sd-term dd code {
  font-family: monospace; font-size: 11px; color: #495057;
  background: #fff; border: 1px solid #e9ecef;
  padding: 0 5px; border-radius: 3px; font-weight: 500;
}
.sd-term dd { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.6; }

/* 二级标题 */
.sd-h2 {
  font-size: 16px; font-weight: 700; color: var(--text);
  margin: 26px 0 12px; padding: 0; border: 0;
  scroll-margin-top: 16px;
}
.sd-h2::before {
  content: ''; display: inline-block; width: 4px; height: 14px;
  background: var(--primary, #2563eb); margin-right: 8px; vertical-align: -1px;
}
.sd-h2 code {
  font-family: monospace; font-size: 13px; color: #495057;
  background: #f5f5f5; padding: 1px 6px; border-radius: 3px;
}
.sd-h3 {
  font-size: 14px; font-weight: 700; color: var(--text);
  margin: 18px 0 10px;
}
.sd-h3::before {
  content: ''; display: inline-block; width: 3px; height: 12px;
  background: #868e96; margin-right: 8px; vertical-align: -1px;
}

/* 操作指南卡片 */
.sd-manual {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 12px;
}
.sd-manual-card {
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 16px;
}
.sd-manual-hd {
  display: flex; align-items: center; gap: 10px;
  font-size: 14px; font-weight: 700; color: var(--text);
  padding-bottom: 8px; margin-bottom: 10px;
  border-bottom: 1px dashed var(--border);
}
.sd-manual-num {
  width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
  background: var(--primary, #2563eb); color: #fff;
  font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}
.sd-manual-list {
  margin: 0; padding-left: 18px;
  font-size: 12.5px; color: var(--muted); line-height: 1.9;
}
.sd-manual-list li b { color: var(--text); }
.sd-manual-list li code {
  font-family: monospace; font-size: 11px; color: #495057;
  background: #f5f5f5; border: 1px solid #e9ecef;
  padding: 0 4px; border-radius: 3px;
}

/* 说明表格 */
.sd-vars-table {
  width: 100%; border-collapse: collapse; margin-bottom: 14px;
  background: #fff; font-size: 12.5px;
}
.sd-vars-table th, .sd-vars-table td {
  border: 1px solid var(--border); padding: 7px 12px;
  text-align: left; line-height: 1.6; vertical-align: top;
}
.sd-vars-table th {
  background: #fafafa; font-weight: 600; color: var(--text); white-space: nowrap;
}
.sd-vars-table td { color: var(--muted); }
.sd-vars-table td b { color: var(--text); }
.sd-vars-table code {
  font-family: monospace; font-size: 11.5px; color: #1864ab;
  background: #f0f7ff; border: 1px solid #d0ebff;
  padding: 1px 6px; border-radius: 3px; white-space: nowrap;
}

/* 7 大域卡片 */
.sd-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 10px;
}
.sd-card {
  background: #fff; border: 1px solid var(--border); border-radius: 6px;
  padding: 12px 14px;
}
.sd-card.ext { border-left: 3px solid #adb5bd; }
.sd-card-head {
  display: flex; align-items: center; gap: 8px;
  padding-bottom: 6px; margin-bottom: 8px;
  border-bottom: 1px dashed var(--border);
}
.sd-card-title { font-size: 14px; font-weight: 700; color: var(--text); }
.sd-card-key {
  font-family: monospace; font-size: 11px; color: #868e96;
  background: #f5f5f5; padding: 1px 6px; border-radius: 3px;
}
.sd-card-flag {
  margin-left: auto; font-size: 11px; color: var(--muted);
  padding: 1px 7px; border: 1px solid var(--border); border-radius: 3px;
}
.sd-card-desc {
  font-size: 12px; color: var(--muted); line-height: 1.65;
  margin: 0 0 8px;
}

.sd-row {
  display: flex; gap: 8px; margin-bottom: 4px;
  font-size: 12px; line-height: 1.7;
}
.sd-row-label {
  flex-shrink: 0; width: 60px; color: var(--muted); font-weight: 500;
}
.sd-row-val { display: flex; flex-wrap: wrap; gap: 3px; align-items: center; flex: 1; }
.sd-chip {
  font-family: monospace; font-size: 11px;
  background: #f5f5f5; color: #495057; border: 1px solid #e9ecef;
  padding: 1px 6px; border-radius: 3px;
}
.sd-chip-var { color: #1864ab; background: #fff; border-color: #d0ebff; }
.sd-empty { font-size: 11px; color: #adb5bd; font-style: italic; }

.sd-example {
  margin-top: 8px; padding-top: 8px;
  border-top: 1px dashed var(--border);
}
.sd-example > summary {
  cursor: pointer; font-size: 11px; color: var(--primary);
  list-style: none;
}
.sd-example > summary::-webkit-details-marker { display: none; }
.sd-example > summary::before {
  content: '▸'; display: inline-block; width: 12px;
  color: #adb5bd; transition: transform .15s;
}
.sd-example[open] > summary::before { transform: rotate(90deg); }

.sd-pre {
  margin: 6px 0 0; padding: 10px 12px;
  background: #fafafa; color: #495057;
  border: 1px solid var(--border); border-radius: 4px;
  font-family: monospace; font-size: 11px; line-height: 1.6;
  white-space: pre; overflow: auto;
}

/* 流程示例 */
.sd-demo {
  display: grid; grid-template-columns: 1fr 16px 1fr 16px 1fr;
  gap: 8px; align-items: stretch;
  background: #fafafa; border: 1px solid var(--border);
  border-radius: 6px; padding: 12px;
}
.sd-demo-col { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.sd-demo-label { font-size: 12px; font-weight: 600; color: var(--text); }
.sd-demo-col .sd-pre { flex: 1; min-height: 220px; background: #fff; }
.sd-demo-arrow {
  display: flex; align-items: center; justify-content: center;
  color: #adb5bd; font-size: 16px; font-weight: 700;
}

/* 底部提示 */
.sd-tip {
  margin: 18px 0 4px; padding: 10px 14px;
  background: #fafafa; border: 1px solid var(--border); border-radius: 4px;
  font-size: 12px; color: var(--muted); line-height: 1.7;
}
.sd-tip b { color: var(--text); font-weight: 600; }
.sd-tip code {
  font-family: monospace; font-size: 11px; color: #495057;
  background: #fff; border: 1px solid #e9ecef;
  padding: 0 4px; border-radius: 3px;
}
</style>
