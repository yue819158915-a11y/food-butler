# 美食小助手 · 源码说明

基于 209 家广州餐厅（原清单 111 家 + 网络搜集 98 家）构建的觅食智能体 Web 应用。

## 功能

- **聊天问答**：自然语言提问，支持口语/方言表述（"饮茶"=早茶、"打边炉"=火锅、"平靓正"=便宜）
- **人群口味理解**：「适合老人家」自动翻译为炖汤/清淡/粥/白切鸡等菜品关键词
- **追问对话**：可继续追问推荐理由、店铺细节、对比哪家更合适，AI 基于数据回答
- **推荐结果面板**：聊天提问后右栏自动刷新匹配餐厅卡片（纯展示，无筛选表单）
- **三层降级**：LLM → 本地同义词词库 → 模板，任何情况不会中断服务


## 部署与合规（评审必读）

- **部署**：见 `deploy/README.md`。本应用已容器化（`Dockerfile` + `render.yaml`），镜像内自带全部数据，
  不依赖本机文件、外部数据库或登录体系，可直接部署到 Render / 任何支持 Docker 的平台 / 云服务器。
- **链接**：部署后为公开地址，无需 sharecode，无痕窗口可直接打开；更新请用「重新部署」而非新建服务，保持链接不变。
- **数据合规**：餐厅推荐内容来自公开点评与学生自整理，已去除校名与个人信息；菜谱来自 CookLikeHOC 开源项目并经结构化改写，
  已去除品牌与企业信息，仅保留项目署名；营养数值为估算，不构成医疗建议。
- **LLM Key**：只通过环境变量 `LLM_KEY` 注入，不写入仓库（`config.json` 已在 `.gitignore` / `.dockerignore` 中排除）。

## v5 升级：从「觅食」到「饮食管家」P0（2026-11）

新增「做菜」能力，与原有餐厅检索并存：

| 能力 | 实现 | 边界 |
|---|---|---|
| 多约束推荐 | `core/planner.py`：时间/预算/人数/忌口/健康目标/天气/口味 加权打分，品类轮转保证不全是同一类 | 预算为食材成本估算，非门店价 |
| 结构化菜谱 | `data/recipes.json` 167 道（导入 CookLikeHOC 后清洗） | 用量由门店配方反抽，`verified=false` |
| 营养标注 | `data/ingredients_nutrition.json` + `core/nutrition.py`，按食材克数累加 | 估算值，带 confidence 与 missing |
| 健康评分 | 油盐/蛋白/纤维/热量 启发式打分 A/B/C | 非医学建议 |
| 天气情境 | `core/daily.py`，Open-Meteo 免 Key，15min 缓存 | 失败自动降级为时段推荐 |
| 过敏原硬过滤 | `recipes.has_allergen`，永不放宽 | 依赖菜谱标签推导，不能替代医学判断 |

数据管线：

```bash
python tools/import_cooklikehoc.py   # 解析 CookLikeHOC → data/recipes.json（含去重、份量归一、品牌清洗）
python tools/validate_recipes.py     # 10 项校验，errors 阻断入库
```

新增接口：`GET /api/recipes`、`GET /api/recipes/{id}?servings=`、`GET /api/recommend`；
`POST /api/chat` 增加 `mode="cook"`（正则预分流，不影响餐厅检索评测基线）。

**数据来源与免责**：菜谱来自开源项目 CookLikeHOC（像老乡鸡那样做饭），经结构化改写后使用；
营养为按食材表估算，非医学建议；过敏原信息来自食材推导，请以实际配料表为准。

## v4 数据更新（2026-11）

| 项 | 改动 |
|---|---|
| 知识库 | 111 → **209 家**，新增 98 家（荔湾/越秀老字号、云吞面、煲仔饭、大排档、潮汕牛肉锅、异国菜、农庄等） |
| 合并脚本 | `tools/merge_v3.py`：按餐厅名去重、类型归一到 11 类目、价格解析（含"300以上"开口区间）、行政区映射到现有 region 方案，幂等可重跑 |
| 新数据表 | `data/广州美食总表-209家.xlsx`（原始表，表头在第 4 行） |
| 生成提示词 | 明确禁止编造营业时间/地址/电话，只能使用候选数据里已有信息 |

## v3 改动（2026-11）

| 项 | 改动 |
|---|---|
| 名称 | 应用名改为「美食小助手」，去掉"华工/五山校区"定位，副标题为"广州觅食地图" |
| 右栏 | 删除类目/价格/地点/关键词/排序筛选表单，只保留「推荐结果」计数 + 餐厅卡片；聊天返回的条件自动驱动右栏刷新 |
| 并发防护 | 右栏请求加序号守卫，连续快速提问时只渲染最后一条消息的结果 |
| 意图识别 | 新增规则：对比/评价类问题（"哪家好""更适合带爸妈""值不值得"）一律走对话模式，不重新筛选 |
| 兼容 | `relaxed` 字段统一归一化，兼容结构化对象 / 字符串 / false 三种历史格式 |


## v2 改动（2026-11）

| 项 | 改动 |
|---|---|
| 价格判定 | 不再用"区间相交"，改用 `price_mid`（典型人均）。「人均40以下」不会再出现人均 55 的店 |
| 价格窗口 | 「人均X」→ `[0.7X, 1.15X]`，抽成 `core/rules.py` 的纯函数，prompt 与词库共用一份 |
| 关键词 | AND 改 OR，按命中词数排序。「海鲜 便宜 新鲜」不再越说越少 |
| 无解兜底 | 不再返回全库。按维度分级放宽（类目永不放宽 / 关键词最先放弃 / 价格阶梯 +15% → +50%），并如实告知放宽了什么 |
| 无解时 | 返回 0 条 + 最接近的 3 家 + 差距量化（"人均 60，超出 55 元"） |
| 定位 | 口径改为"广州觅食地图"，不再声称"五山校区周边" |
| 评测 | 新增 `eval/`：17 用例 + 规则单测 + 基线对比。严格违反率 13.0% → 0.0% |

### 价格字段

`data/restaurants.json` 每条含：

| 字段 | 含义 |
|---|---|
| `price_raw` | 原始文案，如 `40-70` |
| `price_min` / `price_max` | 区间边界 |
| `price_mid` | **典型人均**（区间中点），筛选按它判定 |
| `price_floor` | 最便宜能吃多少（"随便吃点"类需求用） |

补字段：`python tools/migrate.py`（幂等，自动备份 `.bak`）

### 评测

```bash
python eval/run.py            # 检索层 17 用例，秒级
python eval/run.py --compare  # 与基线对比
python eval/test_rules.py     # 取窗与价格解析的纯函数单测
```

## 安全说明

**`config.json` 里的 `llm_key` 已置空。** 原 Key 曾随源码包分发，应视为已泄露，请到智谱后台重置。

恢复 AI 对话有两种方式：

1. 环境变量（推荐，不落盘）：`LLM_KEY=你的key python app.py`
2. 写进 `config.json` 的 `llm_key` 字段（仅本地开发用）

Key 优先级：请求头 `X-LLM-Key` > 环境变量 `LLM_KEY`/`ZHIPU_API_KEY` > `config.json`。
无 Key 时自动降级到本地词库模式：筛选、放宽提示、无解兜底全部正常，只有闲聊与自由生成话术不可用。

## 技术栈

- 后端：Python 3.11 + FastAPI + uvicorn（单端口，同时托管页面与 API）
- 前端：原生 HTML/CSS/JS，无构建步骤
- LLM：智谱 GLM（`glm-4-flash`，OpenAI 兼容接口）
- 数据：Excel 预处理为 JSON

## 目录结构

```
scut-food-agent/
├── app.py               # FastAPI 入口：静态托管 + API 路由
├── preprocess.py        # 一次性脚本：xlsx → data/restaurants.json
├── requirements.txt     # Python 依赖
├── config.json          # LLM 配置（含 API Key，对外分享前注意！）
├── data/
│   ├── restaurants.json # 预处理后的餐厅数据（可直接改这个再重启）
│   └── 四季度精选美食推荐v4.0.0.xlsx  # 原始数据表
├── core/
│   ├── store.py         # 数据加载 + 结构化筛选（类目/价格/地点/关键词）
│   ├── nlu.py           # LLM 意图识别（filter/chat 双模式）+ 本地规则降级
│   ├── synonyms.py      # 粤语/口语同义词词库（约 40 词条，可自行扩充）
│   └── generator.py     # 回复生成：筛选推荐 + 自由对话，普通话输出
└── web/
    ├── index.html       # 单页：左聊天 + 右筛选面板
    ├── app.js           # 前端逻辑（对话、面板联动、餐厅卡片）
    └── style.css        # 样式
```

## 快速启动

```bash
pip install -r requirements.txt
python app.py            # 默认 8000 端口，也可 PORT=9000 python app.py
# 浏览器打开 http://localhost:8000
```

## 配置 LLM

`config.json`：

```json
{
  "llm_base_url": "https://open.bigmodel.cn/api/paas/v4",
  "llm_model": "glm-4-flash",
  "llm_key": "你的智谱API Key"
}
```

- Key 也可不配：页面右上角 ⚙️ 可让每个访客自带 Key（存浏览器 localStorage，随请求头传输）
- 无任何 Key 时自动降级为本地词库模式，功能照常

⚠️ **当前 zip 内的 config.json 含真实 API Key，请勿公开传播；对外分享请把 llm_key 置空。**

## 修改指引（常见需求）

| 需求 | 改哪里 |
|---|---|
| 新增/修改餐厅数据 | 改 `data/restaurants.json`（或改 xlsx 后跑 `python preprocess.py`） |
| 调整 AI 理解规则 | `core/nlu.py` 的 `SYSTEM_PROMPT` |
| 扩充口语词库（降级模式） | `core/synonyms.py` 的 `CATEGORY_WORDS` 等词表 |
| 调整回复风格 | `core/generator.py` 的 prompt 与模板 |
| 改界面 | `web/` 下三个文件 |
| 类型归一化规则 | `preprocess.py` 的 `TYPE_MAP`（52 种原始类型 → 11 类目） |

## API 一览

- `GET /api/restaurants?category=&price_min=&price_max=&location=&q=&sort=&limit=` — 结构化筛选
- `POST /api/chat` — `{message, history}` → 条件解析 + 筛选 + AI 回复
- `GET /api/meta` — 类目/地点列表、Key 配置状态
- `GET /api/health` — 健康检查
