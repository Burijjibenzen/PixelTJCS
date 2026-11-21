# Roamchestra 代码说明文档（交接指南）

> 写给接手项目的同学/队友。读完本文档，你应当能独立跑通系统、看懂每个
> 模块在干什么、并且知道往哪里加新功能。
> 配套阅读: `docs/DESIGN.md`（需求与设计）、`docs/MAP_SPEC.md`（地图格式规范）、
> `README.md`（快速开始）。M6 的事件生命周期、调度、旁听和上帝模式见 [M6 交接说明](M6.md)。

## 1. 项目一句话

10 个校园 NPC 在济事楼三楼、四楼和智信馆二楼活动。Godot 依据每层的实际地板和障碍物移动角色。连接后端后，角色按日程或临时指派行动，并上报实际走到的楼层和房间。离线时可直接观看本地演示。

## 2. 当前状态

| 能力 | 状态 | 入口 |
|---|---|---|
| 人格卡系统（10 张） | ✅ | `personas/*.json` |
| 公开人格卡与可视化资料页 | ✅ | `GET /npcs/{name}/profile`、`persona_panel.gd`；秘密字段不进入公开响应，详见 `M4.1.md` |
| 公告栏发布、查看与 NPC 阅读 | ✅ | `noticeboards.py`、`notice_board.gd`；地图点击与上帝模式入口，见 [M6](M6.md#3-公告旁听与角色记忆) |
| NPC↔NPC 多轮对话 | ✅ | `POST /conversation`、`run_demo.py` |
| 记忆写入 + 向量检索 | ✅ | `backend/memory.py` |
| 遗忘三机制（衰减/淘汰/反思） | ✅ | `GameService.advance_time()` |
| 关系矩阵 + LLM 评估 | ✅ | `backend/relations.py` |
| 游戏时钟 + 时间加速 | ✅ | `backend/world.py` |
| 玩家身份、会话与NPC交谈 | ✅ M4.2 已实现，待完整可见窗口验收 | `player_mode.py`、`player_chat.gd`，默认关闭；完整记忆与关系，详见 `M4.2.md` |
| Godot 前端独立运行（地图/寻路/移动/调试视图） | ✅ M2.1 | `frontend/`（§6/§7） |
| NPC 自由移动 | 三层地图支持楼内、跨层及跨馆寻路 | `frontend/scripts/npc.gd` |
| 前端↔后端联动（/world 驱动移动 + 位置上报） | ✅ M2.2 | `frontend/scripts/api_client.gd` + `POST /world/report`（§12 第 7 条） |
| LangGraph 编排迁移 | ✅ M3 | `scheduler.py` / `agent_runtime.py` / `conversation.py`，详见 `M3.md` |
| 结构化事件与当前活动 | ✅ M6 | `backend/game_events.py`、`backend/event_runtime.py`；角色时间线和活动因果关系 |
| 三档调度、邀约与跟踪 | ✅ M6 | `backend/activity_priority.py`、`backend/daily_planner.py`、`backend/scheduler.py` |
| 旁听与个人回忆 | ✅ M6 | `backend/social.py`、`backend/npc.py`；按实际位置听取台词，分别形成记忆 |
| 角色状态栏与上帝模式 | ✅ M6 | `frontend/scripts/character_status.gd`、`frontend/scripts/notice_board.gd` |

## 3. 快速开始

Windows 先在仓库根目录创建 `.venv` 并安装依赖，再双击 [Start-Roamchestra.bat](../Start-Roamchestra.bat)。后端健康检查通过后脚本会启动 Godot。 [路径配置和联调步骤](M6.md#5-启动和验收)

```bash
# 1) 环境: Python 3.11（3.10+ 均可）
cd Roamchestra
pip install -r requirements.txt

# 2) 配置 key: 把 .env.example 复制为 .env，填 LLM_API_KEY
#    （同济 AGI 网关 https://agi.tongji.edu.cn/v1，OpenAI 兼容协议）

# 3) 跑命令行演示（推荐先跑这个，3 场对话 + 时间快进遗忘 + 记忆面板）
python scripts/run_demo.py --quick   # --quick 为 1 回合快速模式

# 4) 起 HTTP 服务（供前端/调试）
uvicorn backend.api:app --reload --port 8000
#    打开 http://127.0.0.1:8000/docs 有自动生成的交互式接口文档

# 5) 跑 Godot 前端（M2.1，独立运行，无需 Python/key；完整说明见 §7）
#    ① 下载 Godot 4.5.2 stable 标准版（godotengine.org/download/archive/，绿色免安装）
#    ② 双击 exe → 导入 frontend/project.godot → 编辑器里按 F5
#    ③ 命令行冒烟测试（无窗口，跑 600 帧≈10 秒自动退出）:
#       & <Godot exe路径> --headless --path frontend --quit-after 600 *>> log.txt
```

演示脚本会依次执行: 导师召见 → 实验室吐槽 → 茶水间情报交换 →
快进 3 天（触发遗忘与反思）→ 打印记忆面板与关系矩阵。
**注意**: 全程调用大模型，完整跑一次约 3~8 分钟；`--quick` 减半。
若网关不可用，对话自动降级为预设台词（链路照常演示，见 §11.4）。

## 4. 目录结构

```
Roamchestra/
├── .env                  # 密钥（已 gitignore，绝不入库）
├── .env.example          # 配置模板
├── requirements.txt      # 依赖（5 个包，见 §11.5）
├── personas/             # ★ 人格卡目录（10 张 JSON，新增角色在这里加）
│   ├── chen_juan.json    #   陈卷  博四卷王
│   ├── lin_momo.json     #   林摸摸 硕三摸鱼
│   ├── su_xiaoxin.json   #   苏小新 研一萌新
│   ├── zhou_chong.json   #   周冲  研二竞赛
│   ├── he_kuaixun.json   #   何快讯 研二情报通
│   ├── wu_li.json        #   吴鲤  研三考公
│   ├── fang_zhe.json     #   方哲  博二哲学青年
│   ├── yan_zheng.json    #   严正  教授/大导师
│   ├── wen_yan.json      #   温言  青年副导
│   └── qian_guan.json    #   钱管  教务秘书
├── backend/              # ★ 全部后端代码（依赖方向见 §5 开头）
│   ├── config.py         # 全局参数（模型名/遗忘阈值/权重），改参数只动这里
│   ├── logger.py         # 统一日志（控制台 + logs/ 文件）
│   ├── llm.py            # LLM 客户端: chat / chat_json / embed
│   ├── persona.py        # 人格卡加载 → 提示词渲染
│   ├── memory.py         # 自研记忆服务（核心: 存储/检索/遗忘/反思）
│   ├── relations.py      # 关系矩阵（SQLite 持久化）
│   ├── world.py          # 游戏时钟/区域地图/位置/事件/日程
│   ├── npc.py            # NPC Agent（说话/记忆/反思的原子能力）
│   ├── conversation.py   # NPC↔NPC 对话引擎（编排一次完整对话）
│   ├── service.py        # 装配根（把上面所有组件串成 GameService）
│   └── api.py            # FastAPI 接口层（薄壳，逻辑都在 service）
├── scripts/
│   └── run_demo.py       # 命令行演示（答辩入口）
├── frontend/             # ★ Godot 4.5 前端工程（M2.1，模块详解 §6，使用方法 §7）
│   ├── project.godot     #   工程入口: 1280x720 · GL Compatibility 渲染 · autoload Game
│   ├── scripts/          #   game_data(寻路+房间) · npc(状态机) · camera(镜头) · main(装配+双驱动)
│   │                     #   api_client(后端轮询/上报/快进, M2.2) · debug_draw(区域调试层)
│   ├── scenes/           #   main.tscn · npc.tscn · char_1~4.tres(4 套角色各 38 帧动画)
│   ├── data/npc_roster.json  # 10 NPC: id(与后端 persona 同名)/贴图编号/出身房间/色调
│   ├── assets/map/             # 三个楼层的场景、房间清单和导航网格
│   └── tools/            #   extract_map_data.py(地图→网格/区域) · gen_char_frames.py(帧动画生成)
├── docs/
│   ├── CODE_GUIDE.md     # 本文档
│   └── DESIGN.md         # 需求与设计方案
└── data/                 # 运行时生成: memory.db（已 gitignore）
```

## 5. 模块详解（按依赖方向，箭头=import）

依赖方向一图流（**禁止反向依赖**，改代码前先看这张图）:

```
api.py ──┐
         ├──> service.py（装配根）──>┬─> conversation.py ──> npc.py ──┬─> memory.py ──> llm.py ──> config.py
run_demo.py ┘                        ├─> relations.py ────────────────┘              │
                                     ├─> world.py <──────────────────────────────────┘
                                     └─> persona.py <────────────────────────────────┘
```

### backend/config.py —— 全局参数
三个 dataclass: `LLM`（模型名/max_tokens/温度）、`MEMORY`（容量/半衰期/阈值/
三因子权重/反思间隔）、`WORLD`（开局时间/对话回合数/冷却）。
优先读 `.env` 环境变量。**调参只改这里**，例如想"忘得更快"就把
`half_life_minutes` 从 480 调小。

### backend/llm.py —— 大模型客户端（唯一出口）
- `chat(system, user)` → 正文文本；处理了**推理模型的坑**（见 §11.1）
- `chat_json(system, user, schema)` → Pydantic 对象；提示词注入 JSON schema +
  正则抽取 + 校验失败带错误重试一次。所有结构化决策走这里
- `embed(texts)` → 向量列表（Qwen3-Embedding）
- 任何失败返回 None/[]，不抛异常——上层兜底（§11.4）

### backend/persona.py —— 角色数据层
`PersonaCard.from_file()` 读 JSON 卡；`render_system_prompt()` 把卡渲染成
系统提示词（所有说话请求的"人设底座"）；`render_secret_hint()` 输出秘密
提示（**只注入本人上下文**，实现"只有我知道、被好友套话才可能说漏"）。
`load_all_personas()` 扫描 personas/ 目录，坏卡跳过不拖累全局。

`public_profile()` 明确列出公开字段，返回副本，不包含 `secrets`、内部关系配置或原始提示词。`GameService.public_npc_profile()` 补充世界地点与日程名称，API响应模型再限制输出结构。Godot的“角色资料”页复用已有角色选择器，支持离线、缓存、长文本与实时地点，详见 [M4.1.md](M4.1.md)。

### backend/memory.py —— 自研记忆服务 ★核心★
一条记忆的生命周期（`MemoryItem` ↔ SQLite 表 `memories`）:

```
add() 写入(算向量) → recall() 三因子打分检索 → full_forget_pass() 遗忘
                                                  └→ maybe_reflect() 反思压缩
```

- **检索打分**（Generative Agents 风格）:
  `score = 0.20·新近度 + 0.30·重要性 + 0.50·相关度`（权重在 config）
  新近度 = `0.5^(Δ游戏分钟/半衰期)`；相关度 = 向量余弦（失败降级为 2-gram 重叠）
- **遗忘机制 1 时间衰减**: 强度 = `0.6·新近度 + 0.4·重要性`，低于 0.30 →
  `forgotten=1`，原因 `strength_decay`
- **遗忘机制 2 容量淘汰**: 活跃记忆超 60 条 → 淘汰强度最低者，原因 `capacity_evict`
- **遗忘机制 3 反思压缩**: 未反思记忆攒够 4 条 → LLM 压缩成一条洞察
  （`type=reflection`，高重要性），原始记忆 importance 减半打 `reflected` 标记
- **遗忘 ≠ 删除**: 全部留痕（forgotten_at + forget_reason），
  供前端"遗忘档案"面板展示——这是课程核心卖点

### backend/relations.py —— 关系矩阵
`relations[a][b] ∈ [0,100]` 对称存储；`apply_delta()` 带原因写入
（"为什么变了"是前端关系面板的重点展示项）；`level()` 把分数映射成
"莫逆之交/好朋友/普通同学/有点尴尬/互相看不顺眼"。

### backend/world.py —— 世界事实
- `AREAS`: 10 个区域的字典（id/名称/楼/层/类型），加区域直接往里加
- `GameClock`: 游戏分钟数，`fmt()` 输出"第 D 天 HH:MM"
- `World.npc_area`: {角色: 区域id}。M1 无移动系统，位置由 `set_area()`/日程
  显式设定；`co_located_pairs()` 已实现同区域判定，是 M3 调度器的地基
- HTTP动态日程由 `social.py` 装配 `daily_planner.py`，后者只依赖活动目录、配置和世界事实，不被World/底层记忆模块反向导入。模型建议通过 `agent_runtime.py` 调用统一 `llm.py`；参数集中于 `config.ACTIVITY`。实际位置仍来自Godot上报，`activity_targets`只保存锁定的目的地。
- `inject_event()`: 事件流水（组会通知、召见等）
- `can_chat()/mark_chatted()`: 对话冷却（同一对 120 游戏分钟内不重复触发）

### backend/npc.py —— NPC 原子能力
`NPCAgent` = 人格卡 + world + llm + memory 句柄。只提供单个角色的原子操作:
`opener()` 开场白、`speak(history)` 接话、`save_dialogue_memory()` 以**第一人称
视角**评定并保存对话记忆（LLM 出 content/importance/emotion）、
`maybe_reflect()` 反思。没有流程编排——编排全在 conversation.py。

### backend/conversation.py —— 对话引擎 ★灵魂★
`ConversationEngine.run(a, b, trigger)` 完整流程:
```
校验(存在/冷却) → 调度同区域 → A opener → 循环 max_rounds 轮:
    B 接话 → A 接话                ← 各自带人设+记忆+处境的提示词
→ 双方各自 save_dialogue_memory（视角独立！同一场对话两种记忆）
→ LLM 评估关系影响 RelationImpact(delta, reason) → apply_delta
→ 记录冷却 → 返回 transcript + memories + relation
```
这就是基座项目缺的"真对话"：NPC 互相感知、内容连续、有记忆沉淀。

### backend/service.py —— 装配根
`GameService` 把所有组件 new 出来并接线（含 fresh_db 清库、初始关系注入），
提供高层动作: `run_conversation / advance_time / goto / chat_with_npc` +
查询: `npc_list / memory_panel / relations_snapshot / world_snapshot`。
**api.py 和 run_demo.py 都只调它**，保证两条入口行为一致。

### backend/api.py —— HTTP 薄壳
FastAPI 路由 → 一行调 GameService。全局单例 `_service`（首次访问时装配）。

## 6. Godot 前端详解（M2.1 新增）

### 6.1 楼层和寻路

Godot 为每层创建独立的 A* 网格。程序从楼层目录的 `grid.json` 读取静态障碍，从 `manifest.json` 和实际地板图层读取房间归属、落脚点与楼梯。济事楼三楼、四楼各有 9 个区域，智信馆二楼有 22 个。两个楼层可以使用同一个房间编号，例如三楼的 `R05` 是东侧机房，四楼的 `R05` 是学术讲座教室。

NPC 收到目标后，先走到可达的楼梯落脚点，在另一层的对应入口继续寻路。济事楼东、西楼梯都可往返三、四楼。跨馆行程在济事楼三楼西楼梯与智信馆二楼西端楼梯之间转场，省略室外步行。切换观察楼层时，其他楼层的角色仍按原路线移动。

### 6.2 代码和数据入口

| 文件 | 用途 |
| --- | --- |
| `frontend/scripts/game_data.gd` | 读取每层的网格、房间和楼梯，计算各段路线。 |
| `frontend/scripts/npc.gd` | 执行房内闲逛和定点行程。按照目标楼层、房间走完路线后发出到达信号。 |
| `frontend/scripts/main.gd`、`camera.gd`、`debug_draw.gd` | 管理楼层观察、镜头跟随、调试视图和界面装配；活动信息在角色资料中查看。 |
| `frontend/scripts/api_client.gd` | 读取后端日程目标并上报角色实际位置。连接中断后恢复本地演示。 |
| `frontend/data/npc_roster.json` | 配置现有十名角色的贴图、出生位置及离线演示目的地。 |
| `frontend/assets/map/<楼层>/manifest.json` | 查看该层的房间编号、名称和楼梯。NPC 目标同时使用楼层编号和房间编号。 |

三份可编辑源图、图层命名与地图重建命令见[地图交接](../map_sources/README.md)。角色帧仍可通过 `frontend/tools/gen_char_frames.py` 根据原角色贴图生成。

教师和教务继续使用旧日程中的办公室别名。当前严正暂用济事楼三楼中部机房，温言暂用三楼东侧机房，钱管暂用四楼中部机房。团队确定具体工位后，只需在角色名册中修改对应人物的 `home_map` 和 `home`，保留 `office_alias`，重启前后端即可。已有角色日程和旧演示指令会自动使用新地点。[角色名册](../frontend/data/npc_roster.json)

## 7. Godot 使用方法（从零到运行）

### 7.1 安装（一次性，5 分钟）
- **Godot 不是 pip 包**——它是独立游戏引擎，绿色版 exe 免安装、不写注册表，与 Python
  生态无关（GDScript 语法像 Python 但不是 Python；pip 上叫 "godot" 的第三方包**不是**
  引擎本体，别装）。本项目的 pip 依赖只涉及后端。
- 下载：官网归档页 `godotengine.org/download/archive/` → **Godot 4.5.2** → Windows 的
  **"Godot Engine" 标准版**（不要 .NET/C# 版，那需要装 Visual Studio 且本项目用不到），
  得到约 55MB 的 `Godot_v4.5.2-stable_win64.zip`，解压即用。
- 版本怎么选：**stable**=正式稳定版（选它）；rc1/rc2=发布候选测试版（不选）；
  beta/alpha/dev=开发版（不选）。4.5 / 4.5.1 / 4.5.2 尾号是补丁号（只修 bug），
  **选最新的 4.5.2**。
- 版本要求：本项目地图是 Godot 4.3+ 的瓦片二进制格式，**引擎 ≥4.3 均可**，推荐 4.5.2。
  用更新版本（如 4.7）打开会弹"将修改为 Godot 4.7"——**点确认即可**，它只把
  project.godot 里的版本标记从 4.5 改成 4.7，不影响任何场景/脚本/地图。

### 7.2 运行（编辑器方式，日常用）
1. 双击 Godot exe → 项目管理器 → 点 **导入** → 选择 `frontend/project.godot` →
   **导入并编辑**；
2. 首次打开自动导入资源（几秒~几十秒）；
3. 按 **F5** 运行（主场景已在 project.godot 配置为 scenes/main.tscn）；
4. 进入主场景后，用楼层选择器观察三张地图。离线也可以跟随 NPC 看到完整行程；从「浏览楼层地图」按 Esc 可返回 NPC 模拟。

### 7.3 操作与验收点
| 按键 | 功能 |
|---|---|
| 滚轮 | 缩放（0.8~5 倍） |
| **鼠标左键拖动地图** | 平移镜头，拖动超过阈值后取消对人物或告示栏的点击。 |
| **WASD／方向键、E、P** | 玩家模式下行走、近距离交互、回到玩家镜头。踩楼梯箭头即可换层。 |
| **1 / 2 / 3** | 切换观察济事楼三楼、四楼或智信馆二楼。 |
| 空格 | 循环跟随 10 个 NPC，角色转场时镜头随它切换楼层。 |
| 鼠标左键点击人物或姓名 | 跟随该人物，同时展开社交观察并切换到其角色资料；面板内操作不会点中背后的人物。 |
| 功能窗口标题栏 | 拖动窗口。再次点击入口或点 X 关闭。 |
| Esc | 取消跟随 |
| **F3 或 V** | 显示当前楼层的房间地板、名称、门口标记和 NPC 当前行走段的目标（部分笔记本 F 键默认是多媒体键，按 Fn+F3 或直接按 V） |
| **快进 1 小时／4 小时／3 天**（右上角） | 连续加速指定游戏时长，完成后恢复原倍率并触发维护。定时快进需连接后端。 |
| **暂停和倍速选择**（右上角） | 原速为现实 1 秒对应游戏 10 分钟，时钟、移动与动画支持 ×1／×2／×4／×6。暂停时冻结游戏时钟和 NPC 对话进度。 |

离线可跟随周冲从三楼东侧机房前往四楼学术讲座教室。打开区域调试后，可以看到角色先走到楼梯，到四楼后再走向教室。也可以观察智信馆二楼图书分馆的到达效果。

### 7.4 用 Godot 验证 NPC 寻路

从仓库根目录打开 PowerShell，将第一行设为本机的 Godot 可执行文件。首次运行先导入资源。

```powershell
$godotExe = "E:\homework\JishiTown\tools\godot-4.5\Godot_v4.5-stable_win64_console.exe"
& $godotExe --headless --path frontend --import
& $godotExe --headless --fixed-fps 60 --path frontend --script (Resolve-Path scripts/check_campus_navigation.gd).Path
```

脚本检查 40 个房间落点的连通性，再运行主场景，让周冲走到四楼讲座教室、智信馆图书分馆，最后回到三楼东侧机房。每一站都核对实际位置、换层顺序和到达信号，全部通过时输出 `PASS`。

### 7.5 Godot 常见问题（另见 §13 排查表）
| 症状 | 处理 |
|---|---|
| 弹"将修改为 Godot 4.7" | 正常升级提示，点确认（§7.1） |
| uid 警告 / "uid not found" | 网页绘图工具导出的场景带自制 uid 字符串，无害；Godot 按路径解析，编辑器保存一次后自动生成正确缓存 |
| F5 提示 "No main scene defined" | 文件面板右键 `scenes/main.tscn` → 设为主场景 |
| 中文显示方块 | Godot 4 自动回退系统字体（微软雅黑），正常 Windows 不出现 |
| NPC 走进桌柜的占地 | 检查家具是否放在障碍层，重建该楼层的通行网格，然后重新运行游戏。 |
| 改了区域名没生效 | 核对该层 `manifest.json` 中的房间名称，重启 Godot 和后端以加载新清单。 |

## 8. 一次演示调用的完整数据流（跟着走一遍就懂了）

```
run_demo.py
 └─ GameService(fresh_db=True)            清库 → 装配 → 加载10卡 → 初始关系
 └─ service.goto("严正","zx4_yan")        世界: 设置位置
 └─ service.run_conversation("严正","陈卷",trigger)
     └─ engine.run(...)
         ├─ npc_a.opener(trigger)         LLM: 人设+处境+记忆 → 开场白
         ├─ npc_b.speak(history) ×2       LLM: 接话
         ├─ npc_a.speak(history) ×2       LLM: 接话
         ├─ 双方 save_dialogue_memory()   LLM: 视角评定 → memory.add(算向量入库)
         └─ _assess_relation()            LLM: delta+原因 → relations.apply_delta
 └─ service.advance_time(3天)             时钟+4320分钟
     ├─ 每个NPC.maybe_reflect()           攒够4条 → LLM压缩成洞察（原记忆降权）
     └─ memory.full_forget_pass()         衰减淡忘 + 容量淘汰（返回统计）
 └─ service.memory_panel("陈卷")          活跃/遗忘档案/洞察 三视图打印
```

## 9. HTTP API 一览（uvicorn 启动后，交互文档在 /docs）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/world` | 世界快照（时间/全员位置/最近事件）；每个 NPC 带有当前位置 `map_id` / `room_id` 与目的地 `target_map_id` / `target_room_id` |
| GET | `/npcs` | 角色名单（身份/性格/位置） |
| GET | `/npcs/{name}/profile` | 公开人格、目标、地点和基础日程。每个日程项含时间、地点及活动短语。字段使用白名单，未知角色返回 404 |
| GET | `/npcs/{name}/activities` | 当前活动/原因/状态和最近20项安排，详情见动态日程交接 |
| POST | `/npcs/{name}/activities/{id}/cancel` | 取消活动；已经开始处理的会面返回409 |
| GET | `/npcs/{name}/timeline` | 角色已知的事件，支持 `before` 游标和 `limit`，默认每页 30 条 |
| GET | `/npcs/{name}/memory` | ★个人记忆面板: `active`/`forgotten`/`reflections` 三视图 |
| GET | `/relations` | 全员关系矩阵（0~100，None=陌生人） |
| GET | `/areas` | 区域地图 |
| POST | `/conversation` | ★触发 NPC↔NPC 对话 `{a, b, trigger}` |
| POST | `/world/advance` | ★连续快进 `{minutes, speed?}`（三天=4320，speed默认60游戏分钟/现实秒），完成后恢复原速并补做维护；请求返回不表示已经到达 |
| POST | `/world/speed` | 以 `{paused?, speed?}` 更新暂停或倍速，`speed` 的单位是游戏分钟／现实秒 |
| POST | `/world/cast` | `{npc, on_stage, world_id}`，让指定 NPC 退场或出演 |
| POST | `/world/reset` | `{world_id, confirm: true}`，清空当前运行数据并重新初始化世界 |
| POST | `/world/goto` | 指派目的地 `{npc, map_id, room_id}`，也接受原有的 `{npc, area}`。当前地点随实际走动更新 |
| POST | `/world/report` | 上报实际位置 `{npc, map_id, room_id, arrived?, activity_id?, x?, y?}`；坐标为地图内像素，区分路过与完整到达，兼容旧客户端 |
| POST | `/chat` | 同步兼容入口，玩家须先加入；`{npc, message, session_id?, request_id?}`，完整结果见第16节 |
| GET / POST | `/player`、`/player/join`、`/player/leave` | 查询、加入或离开玩家模式；默认关闭，见第16节 |
| POST / GET | `/player/chat`、`/player/turns/{turn_id}` | 异步提交一轮对话（202）及查询处理结果，不等待模型阻塞HTTP |
| GET | `/player/history`、`/player/memory` | 本人会话历史、活跃/遗忘记忆，均需`session_id` |
| GET | `/noticeboards` | 三张地图中的真实公告栏、位置与编号 |
| GET / POST | `/noticeboards/{board_id}/notices` | 查看或发布公告，发布只需要正文 `content`（最多 500 字）。NPC 实际读完后才形成记忆 |
| DELETE | `/noticeboards/{board_id}/notices/{notice_id}` | 从公告栏撤下公告，已获得的记忆和历史事件仍保留 |
| POST | `/noticeboards/{board_id}/read` | `{npc}` 调试阅读请求。须在同层走廊的发现范围内，实际走到板前站定才开始阅读 |
| GET | `/world/whisper/history` | 当前世界已下达的悄悄话列表，包括原文、接收者、下达时间及解析出的活动 |
| POST | `/world/whisper` | `{npc, text}`，将自然语言解析为高档活动并返回已生成的安排 |

前端可视化遗忘面板的数据源就是 `/npcs/{name}/memory` 的 `forgotten` 数组
（含 `forget_reason` 字段: strength_decay=时间衰减 / capacity_evict=容量淘汰）。

## 10. 数据结构

### 10.1 SQLite（data/memory.db，运行时生成）

**表 memories**（MemoryItem 一一对应）:
| 列 | 说明 |
|---|---|
| id / agent / content | 主键 / 属主角色 / 第一人称记忆内容 |
| type | dialogue（对话）/ observation（观察）/ event（事件）/ reflection（洞察） |
| importance / emotion | 重要性 0~1 / 情绪标签 |
| game_time / location | 游戏分钟 / 区域 id |
| participants / tags | 参与者 JSON 数组 / 标记数组 |
| embedding | 内容向量 JSON（Qwen3-Embedding，可能为 null） |
| forgotten / forgotten_at / forget_reason | 遗忘三件套（留痕，不物理删除） |
| reflected | 是否已被反思压缩（importance 同时减半） |

**表 relations**: (a, b, score, last_reason)，主键 (a,b) 对称归一化存储。

### 10.2 人格卡 JSON 字段说明（personas/*.json）

| 字段 | 必填 | 说明 |
|---|---|---|
| name / role / title | ✅ | 唯一名 / student·advisor·staff / 头衔 |
| expertise / mbti / traits / tone | 建议 | 人设要素，进系统提示词 |
| catchphrase / example_lines | 建议 | 口头禅与示例台词（example_lines 兼作离线兜底台词） |
| goals.short_term / long_term | 建议 | 行为动机 |
| secrets | 可选 | 只注入本人上下文；可被高好感角色"套话" |
| schedule | 可选 | `{"HH:MM": 区域id}`，开局按它定初始位置 |
| initial_relations | 可选 | `{角色名: 0~100}`，缺省 50 |

**新增角色 = 复制一张卡改名改内容，零代码改动。**

## 11. 关键设计决策（为什么这么写）

### 11.1 为什么 llm.py 不用 LangChain/LangGraph？
同济网关上的 GLM 是**推理模型**（返回体含独立 `reasoning` 字段），
且 max_tokens 不足时正文 `content` 为 null——这类兼容问题必须在传输层
手工处理（llm.py 的 chat() 有"截断→加倍 token 重试"逻辑）。
M3 迁移路线: conversation.py 的每个步骤（opener/speak/assess/memory/reflection）
天然对应一个 LangGraph 节点；npc.py 的原子能力接口已按此设计，届时
用 `StateGraph` 包住即可，SQLite checkpoint 对应 memory.db。

### 11.2 为什么记忆自研而不用框架内置？
遗忘过程（公式/阈值/压缩）正是课程要**展示**的东西，黑盒组件无法演示。
自研实现全部白盒：`memory.py` 一个文件 375 行，每个数字有出处。

### 11.3 如何区分当前位置和日程目标

后端提供目的地，Godot 把它分解为各楼层的行走段，并用实际地板判断角色位置。后端单独保存行走过程中上报的地点。NPC 经过走廊或楼梯时，原定目的地仍然有效。

HTTP运行时的目的地由动态活动锁定；基础日程翻段不再打断行走。手动指派也作为活动处理，忙碌时等待，实际到达并停留后释放。旧CLI不装配动态调度器，保留原习惯行为。

### 11.4 离线兜底（答辩保险）
LLM 挂了/没 key 时: 说话 → 用人格卡 example_lines 随机兜底；
记忆 → 模板内容（importance 0.4）；关系 → 固定 +1。**演示永不中断**，
只是内容质量下降。判断依据: `llm.chat()` 返回 None。

### 11.5 依赖为什么只有 5 个包？
fastapi/uvicorn（接口）、openai（SDK）、pydantic（校验）、python-dotenv（配置）。
向量检索纯 Python 余弦（记忆条目 <10^3，无性能问题），不引入 numpy/torch。

## 12. 扩展指南（接手后往哪加）

1. **加角色**: personas/ 加 JSON → 完成（关系/日程/秘密全在卡里）
2. **加区域**：在对应的地图中绘制独立地板层，登记到该楼层的 `manifest.json` 并重建网格。为角色安排目的地时，使用该楼层和房间的编号。

3. **加场景**: scripts/run_demo.py 照抄一个"场景块"（goto + inject_event +
   run_conversation）；或运行时 `POST /conversation`
4. **调遗忘速度**: config.py `MEMORY.half_life_minutes`（默认 300，越小忘越快）、
   `forget_threshold`（越大越容易忘）、`capacity`（越小淘汰越狠）
5. **M3 调度器（已实现）**: 继续维护 scheduler.py，循环: `world.co_located_pairs()` →
   按"日程/目标/关系"挑一对 → 调 `service.run_conversation` → `advance_time`
6. **联调寻路**：调用 `POST /world/goto` 指派实际目的地。例如 `{"npc":"周冲","map_id":"jishilou_floor4","room_id":"R05"}` 会让他前往四楼学术讲座教室。查看 `GET /world` 可分别核对当前位置和目的地。

8. **前端地图/动画再生成**: 见 §6.2 和地图交接；改完用 §7.4 的命令行冒烟测试回归

## 13. 常见问题排查

| 症状 | 原因/解决 |
|---|---|
| LLM 返回空/null 正文 | 推理模型思考被截断 → llm.py 已自动加倍 max_tokens 重试；仍失败则把 `.env` 的 `LLM_MAX_TOKENS` 调到 2048+ |
| 403 "当前网络环境不支持" | 网关对出口 IP 有风控：关掉 Clash 的 TUN/系统代理，或在 Clash 规则里把 `agi.tongji.edu.cn` 设为 DIRECT；llm.py 已强制 `trust_env=False` 绕过代理环境变量 |
| 500 "model字段值不正确" | 模型名**大小写敏感**，必须与 `GET /v1/models` 返回完全一致（如 `GLM-5.3-Flash` 而非 `glm-5.3-flash`）；网关可能轮换模型名，以 /models 实时列表为准 |
| 中文请求报 Invalid JSON | 老坑: PowerShell 5.1 的 `Out-File`/`curl -d` 带 BOM → 用 `--data-binary @文件`（文件须无 BOM）；Python SDK 无此问题 |
| 演示跑完"忘了 0 条" | 检查是否用了旧库/时间不够久；正常快进 3 天应遗忘 10+ 条（琐事与被反思降权的旧记忆）；仍为 0 则把 half_life_minutes 再调小或 forget_threshold 调大 |
| 对话被拦截"冷却中" | 同一对 NPC 120 游戏分钟内不能重聊 → `advance_time` 或改 `WORLD.cooldown_minutes` |
| pip 装包慢/失败 | 校园网代理问题 → `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt` |
| git push 走代理失败 | 全局代理已设 7897（Clash Verge）；`github.com` 可用，`git.tongji.edu.cn` 建议单独禁代理 |
| 想重置世界 | 在「上帝模式」中选择「重置世界」并确认。需要保留进度时先停机备份 `data/memory.db`。命令行演示的 `fresh_db=True` 也会清库，运行前先关闭 HTTP 后端。 |
| Godot 打不开工程/弹升级提示 | 引擎版本与排错见 §7.5（"将修改为 Godot 4.7"弹窗点确认即可） |
| Godot 运行报 SCRIPT ERROR | 看错误行的脚本名+行号；若是资源路径问题先删 `frontend/.godot/` 缓存重新导入 |
| 前端看不到任何 NPC | 查 Output 面板是否打印 `[Main] 已生成 10 个 NPC`；没有则 `data/npc_roster.json` 路径/JSON 被改坏 |
| 前端"已连接后端"但 NPC 不挪窝 | 查看日程的等待说明及 `/npcs/{name}/activities`：可能被对话/反思、前序会面或位置上报阻塞。基础作息翻段不会打断在途活动；同室闲逛正常，超时可检查路线或取消重排 |
| room_id_at 等按数字键查字典查不到 | JSON 解析出的数字是 **float**，GDScript 字典里 `3.0` 和 `3` 是不同键——跨 JSON/代码的数字键必须显式 `int()`（M2.2 实际踩坑，详见 game_data.gd `room_id_at` 注释） |

## 14. 历史接力的实现边界与复测

本节保留该轮接力的实现记录。当前气泡和人物占用规则、楼梯操作及复测入口以 [M6 说明](M6.md) 为准。

- 参数仍以 `backend/config.py` 为入口；模型调用继续只经 `llm.py`，没有改写模型、记忆检索或遗忘算法。
- HTTP维持薄路由：连续快进和状态切换放在 `SocialService`，调度循环放在 `Scheduler`；保持现有组件依赖方向。
- `busy` 只表示实际任务处理，气泡计时不参与角色占用；长台词采用原版分段与即时替换，完整内容仍可在日志查看。
- `game_data.gd` 统一提供楼梯入口和目的地。试走中智信馆二楼西端楼梯可返回济事楼三楼；三楼西楼梯可选择上四楼或跨馆，东西楼梯原有功能保留。
- 后端回归为 `python -X utf8 -B -m unittest discover -s tests -p test_social.py -v`。前端专项脚本为 `tests/check_social_ui.gd`、`tests/check_time_flow.gd`、`tests/check_trial_transitions.gd`；实际WebSocket验证为 `tests/run_websocket_smoke.py`。校园路线沿用§7.4，并可通过 `ROAM_TEST_SPEED=6` 验证定时快进倍率。
- 本轮使用独立开发分支，提交明确列出的源码、测试和文档；密钥、运行数据库、日志、Godot缓存及个人工具不入库，不自动推送共享分支。
