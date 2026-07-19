# Terminal Briefing 重设计 — Design Spec

日期:2026-07-15
状态:已与用户逐节确认(§1–§6),本文档为定稿

## 背景与目标

MacroMind 现有 dashboard(`static/index.html`,单文件 vanilla JS + D3,4 个 tab:Map / Heatmap / Regime / Guide)信息完整但偏"研究报告"气质,首屏(世界地图)不直接回答日常使用的核心问题。

本次重设计的定位(与用户确认):

- **日常自用工具**,可读性优先——不是作品集展示
- 首屏回答两个问题,按优先级:**主:"自上次快照以来什么变了?"** / **副:"现在最大的机会在哪?"**
- 数据管线**每周/不定期手动跑**,UI 提供 **Run 按钮**手动触发;GH Actions 定时跑作为可选附录
- 视觉:**Terminal 深色风**(近黑底、等宽数字、琥珀+信号绿),保留浅色模式作为 fallback
- 技术:**vanilla + 拆文件、零构建**,FastAPI 直接 serve 静态目录
- **国家名 UI 层显示中文**;verdict / 指标等术语保持英文

范围:方案二「晨间交易台」= 换皮 + Briefing 首屏 + diff 引擎 + Run 按钮实况 + 机会榜;键盘流降级为 P2 最小集。

## §1 整体架构

### Tab 结构

```
Briefing(新,默认落地页) | Map | Heatmap | Regime | Guide
```

Map / Heatmap / Regime / Guide 保留现有职责,只换主题(§5)。

### 前端拆分(零构建)

```
static/
  index.html            — 壳:header、tab 切换、各视图容器(目标 ~200 行)
  css/theme.css         — Terminal 深色主题 tokens、共享组件样式、浅色 fallback
  js/app.js             — 共享状态(选中国家/资产/tab)、tab 路由、键盘(P2)
  js/api.js             — fetch 封装(signals / regime / history / snapshots / changes / run)
  js/i18n.js            — 国家名映射表(数据 key 英文 → 显示中文)+ ISO 码
  js/views/briefing.js  — 三栏 Briefing:国家轨 + 变化流/机会榜 + 详情检查器 + Run 按钮
  js/views/map.js       — 世界地图(迁移自现有代码)
  js/views/heatmap.js   — 热力图(迁移)
  js/views/regime.js    — 象限图 + 表格(迁移)
  js/views/guide.js     — 阅读指南(迁移)
```

普通 `<script>` 标签按序加载(i18n → api → views → app),**不用 ES modules import 链**,避免任何构建需求。`main.py` 已有的 `StaticFiles` mount 递归 serve 子目录,无需改动挂载。

### 后端新增

`main.py` 从 3 个 endpoint 扩到 7 个(新增 4 个路由):

```
GET  /api/snapshots        — 列出归档快照(id + as_of + meta)
GET  /api/changes          — 默认最新 vs 上一份归档的分级 diff;?base=<id> 可指定基准
POST /api/run              — kick off 管线(body: {"source": "live"|"mock"})
GET  /api/run/status       — 轮询运行状态(§3)
```

新模块:

- `snapshot_store.py` — 快照归档:写入/列出/取最新一对
- `snapshot_diff.py` — diff 引擎,纯函数、可单测(§2)
- `run_manager.py` — 后台运行状态机(§3)

**机会榜不加 endpoint**——排序逻辑在前端算,数据源为现有 `/api/signals` + `/api/regime`。

### 归档格式

```
data/snapshots/<UTC时间戳,如 2026-07-15T203102Z>/
  snapshot.json          — signal 引擎输出的完整副本
  regime_snapshot.json   — regime 引擎输出的完整副本
  meta.json              — {run_id, source, duration_s, quality 摘要}
```

- 归档目录**提交进 git**(每份 ~50KB,周频可忽略;GH Actions commit-back 也依赖这一点)
- **种子基线**:首次使用时若 `data/snapshots/` 为空,`snapshot_store` 把当前已提交的 `snapshot.json` + `regime_snapshot.json` 归档为基线(id 取其 `as_of`),保证第一次 Run 之后就有 diff 可看
- 现有 `history.py`(git 历史 sparkline)保持不动;归档目录是 diff 的唯一数据源,不依赖 git

## §2 Diff 引擎:变化分级

对比两份归档(signal + regime 各自 diff,合并输出),四级:

**L1 · Headline(状态翻转)**
- Regime **verdict 翻转**(如 Unconfirmed → Repricing)
- 信号**方向翻转**:某格 `final` 从 ≤ −0.15 跨到 ≥ +0.15(或反向)

**L2 · 排位变动**
- 机会榜(narrative_gap 排序)变动:进出前 3,或移动 ≥ 2 位
- 某资产类内跨国 `final` 排名移动 ≥ 2 位

**L3 · 漂移**
- 任一格 `|Δ final|` ≥ 0.10
- `|Δ regime_score|`、`|Δ narrative_gap|`、`|Δ confirmation_score|` ≥ 0.10

**L4 · 背景变化**
- evidence 变化:`evidence_count` 或 citations 集合变动;`|Δ rag_confidence|` ≥ 0.20
- **数据源翻转**:provenance 中某输入 live ↔ fallback(quality gate 事件)

低于全部阈值的变动折叠为一行 "N 项微小变动"(`minor_count`),可展开。

### 阈值常量(`snapshot_diff.py` 顶部,v1 初值,注明可调)

```python
SIGN_FLIP_BAND = 0.15    # L1 方向翻转带
DRIFT_MIN = 0.10         # L3 数值漂移下限
RANK_MOVE_MIN = 2        # L2 排名移动下限
RAG_CONF_MIN = 0.20      # L4 rag_confidence 变动下限
TOP_N = 3                # L2 机会榜头部区
```

### 输出契约

```json
{
  "base":   {"id": "2026-07-08T060002Z", "as_of": "..."},
  "target": {"id": "2026-07-15T203102Z", "as_of": "..."},
  "changes": [
    { "level": 1, "kind": "verdict_flip", "country": "Brazil",
      "from": "Unconfirmed", "to": "Repricing",
      "detail": {"confirmation_score": {"from": 0.19, "to": 0.31}},
      "headline": "Brazil: Unconfirmed → Repricing" }
  ],
  "minor_count": 12,
  "unchanged_count": 8,
  "notes": []
}
```

`kind` 枚举:`verdict_flip | direction_flip | opp_rank_move | asset_rank_move | signal_drift | regime_drift | evidence_change | provenance_flip`。`country` 用英文数据 key,前端经 i18n 表转中文。

### 特殊规则

- `methodology_version` 两侧不一致 → **不逐条 diff 数字**,`changes` 为空,`notes` 出横幅提示"方法论版本变更,本期不可比"
- 国家在一侧缺失 → 输出 `kind: "coverage_change"` 条目,不报错
- diff 只对比**两份**快照;三份以上的趋势是 sparkline 的职责,不在本引擎范围

## §3 Run 按钮后端

### 执行模型

`run_manager.py`:`POST /api/run` 启动后台线程,四个 phase 顺序执行,phase 1、2 为独立 subprocess(与 FastAPI 进程隔离):

```
1. signal_pipeline   python signal_engine.py --source {live|mock}
2. regime_engine     python regime_engine.py
3. archive           snapshot_store 归档两份快照 + meta
4. diff              对比上一份归档,缓存结果
```

(phase 3、4 为进程内调用,不必真开 subprocess——状态机粒度不变)

### 状态契约(前端每 1.5s 轮询)

```json
GET /api/run/status
{ "state": "running",              // idle | running | succeeded | failed
  "run_id": "2026-07-15T203102Z",
  "source": "live",
  "phase": {"index": 1, "total": 4, "name": "signal_pipeline"},
  "started_at": "...", "finished_at": null,
  "log_tail": ["...最后 50 行 stdout/stderr..."],
  "error": null,
  "result": null }                  // 成功后:{snapshot_id, headline_count, quality 摘要}
```

### 约束与失败语义

- **粒度:v1 = 4 phase + 实时 log tail。** orchestrator 内部(adapters/quality gates/features)不打结构化日志,adapter 级进度列为 P2,不为进度条重构管线
- 内存锁 + 状态检查;`running` 时再 POST → **409**,按钮置灰。单用户工具;约束:**uvicorn 单 worker**(写入 README)
- 每 phase **超时 10 分钟**;任一 phase 非零退出 → `failed`,完整 log 保留可查
- **仅 4 个 phase 全部成功才写归档** → 失败 run 不产生归档,diff 链天然干净(signal 成功但 regime 失败时,工作区文件已更新但归档不记录;下次成功 run 会一并归档)

## §4 Briefing 视图(三栏终端布局,用户选定 C 方案)

```
┌ header:MACROMIND ▸ BRIEFING · vs <base> (<age>) ─────── [▶ RUN LIVE] ┐
├──────────┬──────────────────────────┬──────────────────────────────┤
│ 国家轨    │ 变化流(CHANGES)          │ 详情检查器(WHY)              │
│ 9 国并集  │ L1→L4 排序,minor 折叠    │ 随点击内容切换                │
│ 状态点+   │ ──────────────           │                              │
│ 覆盖标记  │ 机会榜(OPPORTUNITY)      │                              │
└──────────┴──────────────────────────┴──────────────────────────────┘
```

### 左栏 · 国家轨

- **9 国并集**(signal 6 + regime 6,重叠 3):中文名显示
- 每行:状态点(颜色 = 该国本期最高变化级别:L1 红/绿、L2/L3 琥珀、无变化灰)+ 中文国名 + 覆盖标记(`S` / `R` / `SR`)
- 选中国家 → 中栏变化流过滤到该国;再点同一国取消过滤
- P2 的 `j/k` 在这条轨上移动

### 中栏 · 变化流 + 机会榜

- **CHANGES** 区:按 level 分组(L1 大字报样式 → L4 弱化),每条 = 图标 + 国名(中文)+ headline + 关键数字;`minor_count` 折叠行可展开
- **OPPORTUNITY BOARD** 区(其下):regime 6 国按 `narrative_gap` **降序**,并列时 `confirmation_score` 高者优先——不发明新公式,gap 即"edge 大小"。每行:排名 + 中文国名 + gap + confirmation + verdict 徽章;`Unconfirmed` 带 ⚠ 但**不隐藏**。排序规则与 `snapshot_diff` 的 L2 判定为同一条(gap 降序、confirmation 破并列),规则只有一行 sort,**允许前后端各实现一次**,以换取机会榜零后端依赖
- 空态:归档少于两份 → "需要至少两份归档快照,点 ▶ RUN 生成"

### 右栏 · 详情检查器(三种内容模式)

1. **点变化条目** → "为什么变":前后数字对照、相关驱动(driver 文本)、L4 则显示 evidence/citation 明细
2. **点机会榜行** → verdict 三输入(regime_score / narrative_gap / confirmation_score)+ 跨资产确认通道 ✓✗ 列表 + best_expressions + left_tail_risks
3. **点国家轨** → 该国全景:signal 各资产格(若有 S 覆盖)+ regime verdict 块(若有 R 覆盖)

### Header · Run 按钮与状态

- `idle`:`▶ RUN LIVE`(旁挂 mock 切换,测试用)
- `running`:按钮变进度指示 `phase 2/4 regime_engine`,点击展开浮层看实时 log tail;按钮不可再点
- `succeeded`:短暂显示 `✓ 2 headline changes`,变化流自动刷新
- `failed`:`✗ FAILED` 红色,点击看完整 log
- 常驻:快照年龄(`vs 07-08 · 7d ago`)+ 上次 run 的 quality 摘要(`41✓ 3✗`)

## §5 Terminal 主题

### Design tokens(`css/theme.css` 一处定义)

```css
--bg: #0a0e0a;        /* 近黑绿底 */
--panel: #10150f;     /* 卡片底 */
--text: #d7ded4;
--muted: #6c746a;
--line: #2a2f2a;
--amber: #e8b339;     /* 主强调:标题/边框/激活态 */
--pos: #4ade80;
--neg: #f87171;
```

- 字体:标题/标签 Inter;**一切数字与代码 'SF Mono'/Menlo + `tabular-nums`**
- 浅色模式:现有浅色 palette 保留为 `prefers-color-scheme: light` fallback + header 手动切换按钮

### 现有四视图适配(重皮不重构)

- **Map**:深底;无数据国深炭色;信号色阶两端在暗底调亮一档;图例同步。tooltip/标签经 i18n 表显示中文国名
- **Heatmap**:单元格改**深底 + 色彩边框/文字**(放弃整格填色——暗底整格填色刺眼,可读性优先);数字等宽白字
- **Regime**:象限图深底、散点 verdict 色、dashed 分界线 amber;表格 hover amber 微光
- **Guide**:排版换色;流程图 SVG 经 CSS 变量自动继承

### 国家名中文化

`js/i18n.js` 单一映射表:

```
United States of America → 美国      Canada → 加拿大    China → 中国
Japan → 日本    Brazil → 巴西    Euro Area → 欧元区
Argentina → 阿根廷    Greece → 希腊    Turkey → 土耳其
```

- 数据 key、API、快照文件全部保持英文;**仅展示层转换**
- Map 的 topojson 国名匹配逻辑不变(英文),只在渲染 tooltip/面板标题时转换
- 表中缺失的国名 fallback 显示英文原名(容错,不报错)

## §6 测试策略

pytest,延续现有 `tests/` 风格:

- **`snapshot_diff`(重点)**:verdict 翻转、方向翻转(含恰好压线 0.15/0.149)、排名移动、阈值边界(0.099 vs 0.101)、methodology 不可比横幅、国家缺失容错、空快照
- **`snapshot_store`**:归档写入/列出/取最新一对;种子基线;失败 run 不产生归档
- **`run_manager`**:假 subprocess(`sleep` / `false`)测状态机:running → succeeded、失败传播、409 并发拒绝、超时
- **API 层**:FastAPI TestClient 冒烟测 4 个新路由
- **前端**:无自动化测试(vanilla);每步实施后浏览器人工验证(与用户逐步 review 流程一致)

## P2 清单(核心完成后按余力,可整体砍)

1. 键盘最小集:`j/k` 国家轨移动、`1-4` 资产切换、`?` 帮助浮层(全局 keydown + keymap,约 60–100 行,前提"跨视图共享选中国家"状态已在核心中实现)
2. orchestrator 结构化 stage 打印 → run 进度细化到 adapter 级

## 附录 · GH Actions(可选,默认不启用)

`.github/workflows/weekly-pipeline.yml`:每周一 UTC 06:00 跑 live 管线,成功则 commit 快照 + 归档到 main。手动 Run 与之互不冲突(归档目录按时间戳自然合并)。

```yaml
name: weekly-pipeline
on:
  schedule: [{cron: "0 6 * * 1"}]
  workflow_dispatch: {}
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements.txt
      - run: python signal_engine.py --source live
      - run: python regime_engine.py
      - run: python -c "import snapshot_store; snapshot_store.archive_current(source='live')"
      - run: |
          git config user.name "macromind-bot"
          git config user.email "bot@users.noreply.github.com"
          git add snapshot.json regime_snapshot.json data/snapshots/
          git diff --cached --quiet || git commit -m "chore: weekly live snapshot"
          git push
```

想启用时把该文件放进仓库即可;`snapshot_store.archive_current()` 的函数签名在实现时保持与此一致。

## 实施顺序(供 writing-plans 参考)

用户要求**分步实施、每步完成后 review**:

1. 后端地基:`snapshot_store` + `snapshot_diff` + 单测
2. 后端 API:3 个新 endpoint + `run_manager` + 单测
3. 前端拆分迁移:现有四视图搬进新文件结构(行为不变,旧主题)——先拆后改,保证每步可验证
4. Terminal 主题:theme.css tokens + 四视图换肤 + 中文国名
5. Briefing 视图:三栏布局 + 变化流 + 机会榜 + 详情检查器
6. Run 按钮 + 状态轮询打通
7. P2(按余力):键盘最小集、stage 细化

## 变更记录

- **2026-07-16(实施后调整,用户决定):** 机会榜(OPPORTUNITY BOARD)从 Briefing 中栏迁出,落入新建的 **Summary tab**(Briefing 右侧第一个 tab)。理由:机会榜是静态横截面排名,与 Briefing"自上次快照什么变了"的职责不一致,且国家轨过滤不作用于它。Summary tab 同时新增 **SIGNAL LEADERBOARD**(signal 六国按 composite 降序),点击任一行右侧出详情检查器。Briefing 检查器随之收窄为 change / country 两种模式。
