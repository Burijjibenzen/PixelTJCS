# Roamchestra 代码说明文档（交接指南）

> 写给接手项目的同学/队友。读完本文档，你应当能独立跑通系统、看懂每个
> 模块在干什么、并且知道往哪里加新功能。
> 配套阅读: `docs/DESIGN.md`（需求与设计）、`docs/MAP_SPEC.md`（地图格式规范）、
> `README.md`（快速开始）。

## 1. 项目一句话

10 个校园 NPC（7 学生 + 3 导师/教务）生活在"纪事楼 3F/4F + 智信馆 4F"里，
**彼此对话、各自记事、随时间遗忘、关系动态变化**；玩家模式（M4）允许真人
加入其中。后端是纯 Python 服务（大脑）；前端是独立可运行的 Godot 4.5 工程
（身体，M2.1 已完成：地图分区/寻路/日程移动，尚未接后端，见 §6/§7）。

## 2. 当前状态（M1 + M2.1 完成）

| 能力 | 状态 | 入口 |
|---|---|---|
| 人格卡系统（10 张） | ✅ | `personas/*.json` |
| NPC↔NPC 多轮对话 | ✅ | `POST /conversation`、`run_demo.py` |
| 记忆写入 + 向量检索 | ✅ | `backend/memory.py` |
| 遗忘三机制（衰减/淘汰/反思） | ✅ | `GameService.advance_time()` |
| 关系矩阵 + LLM 评估 | ✅ | `backend/relations.py` |
| 游戏时钟 + 时间加速 | ✅ | `backend/world.py` |
| 玩家↔NPC 对话（最小版） | ✅ | `POST /chat` |
| Godot 前端独立运行（地图/寻路/移动/调试视图） | ✅ M2.1 | `frontend/`（§6/§7） |
| NPC 自由移动 | ⚠️ 前端已有（M2.1 本地日程）；后端仍是逻辑位置 | `frontend/scripts/npc.gd` |
| 前端↔后端联动（/world 驱动移动 + 位置上报） | ✅ M2.2 | `frontend/scripts/api_client.gd` + `POST /world/report`（§12 第 7 条） |
| LangGraph 编排迁移 | ❌ M3（接口已预留） | 见 §11.3 |

## 3. 快速开始

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
│   ├── assets/map/jishilou_floor3/  # 纪事楼3F 地图(7 层.tscn) + floor3_grid/rooms/room_overrides.json
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

### 6.1 架构总览
分工原则：**后端=大脑（权威模拟），Godot=身体（展示+移动）**。M2.1 阶段后端尚未接入，
前端用"本地演示日程"（随机挑 NPC 去随机房间）跑通整条身体链路；M2.2 只换驱动源
（HTTP 轮询后端），`npc.go_to_room()` 接口不变，前端主体零改动。前端无任何 Python
依赖，启动链：`project.godot` → 主场景 `scenes/main.tscn` → autoload `Game`
（scripts/game_data.gd）加载两份 JSON → main.gd 按 `data/npc_roster.json` 生成 10 个
NPC 实例（scenes/npc.tscn）。

### 6.2 四个脚本（scripts/）
| 文件 | 职责 | 关键接口 |
|---|---|---|
| `game_data.gd`（autoload `Game`） | 地图数据+寻路+房间查询。启动时读 `floor3_grid.json`（障碍矩阵）建 **AStarGrid2D**（4 邻接/曼哈顿启发，禁对角穿缝），读 `rooms.json` 建 id→元数据 | `path_between(a, b)` 世界坐标寻路；`nav_world_of(rid)` 房间导航点；`room_id_at(world)` 坐标→房间 id；`random_world_in(rid)` 房内随机点；`room_name/type`；`is_blocked(cell)` |
| `npc.gd`（`class_name NPC`，CharacterBody2D） | 身体：**IDLE/WANDER/WALK** 状态机，速度 55px/s；碰撞盒 8×6（小于半格，保证过 1 格门），NPC 间不互撞（collision_mask=1）；卡墙两级自救：卡 0.6s 以当前路点重寻路 → 卡 1.2s 传送回最近可走格 | `setup(id, name, char_id, home, tint)` 装配；**`go_to_room(rid)` ★M2.2 后端指令直译★**；`go_to(world_pos)`；信号 `arrived(npc)` |
| `camera.gd`（Camera2D） | 自由镜头：**方向键**平移（速度随缩放自适应；WASD 留给 M4 玩家）、滚轮缩放 0.8~5.0、空格循环跟随 NPC（lerp 平滑）、Esc 取消 | `set_target(node)` |
| `main.gd`（main.tscn 根） | 装配：生成 NPC + 注入 ApiClient/DebugDraw；**双驱动**（ApiClient 在线=后端日程，离线=本地演示日程，永不白屏）；右上角日程面板（0.5s 刷新"谁→去哪/已到"）；右下角快进按钮；F3/V 键轮询检测切调试视图 | `cycle_follow()` 空格跟随；`_refresh_schedule_ui()`；`_toggle_debug()` |
| `api_client.gd`（autoload `ApiClient`） | **M2.2 后端联动**：每秒 GET `/world` → `target_grid` 与当前目标不同才 `go_to_room()`；到达上报队列（单飞行防并发）；连续 3 次失败回退本地日程、恢复自动切回；`last_world` 缓存供日程面板 | `setup(npcs)`；`report_arrival(npc)`；`advance_time(分钟)`；信号 `online_changed`/`advance_done` |
| `debug_draw.gd`（DebugDraw 节点，位于 Actors 之后） | 区域调试视图**独立层**——必须画在地图之上（曾画在根节点，根先于子节点渲染，被瓦片完全遮挡）：半透明房间块+粗框+描边大字、门格黄标、NPC 目标线 | `set_enabled(bool)`；`setup(npcs)` |

### 6.3 数据文件
| 文件 | 生成方式 | 内容 |
|---|---|---|
| `assets/map/jishilou_floor3/floor3_grid.json` | `tools/extract_map_data.py` | 寻路网格：tile_size=16、size=[133,70]、`obstacle` 矩阵（'0'=可走）、`room_grid` 房间号矩阵 |
| `assets/map/jishilou_floor3/rooms.json` | 同上（**产物，勿手改**） | 13 个区域：`id`/`name`/`type`(room·corridor)/`area_tiles`/`bbox`/`aspect`/`nav_point` 导航点/`door_cells` 门格 |
| `assets/map/jishilou_floor3/room_overrides.json` | **人工维护** | 提取器的人工修正入口：`extra_walls` 虚拟墙（封闭无门的宽门洞，留 2 格当门）+ `renames` 改名/改类型；改完重跑提取器即生效 |
| `data/npc_roster.json` | 人工维护 | 10 NPC：`id`（与后端 persona 文件同名）/`char` 1~4 贴图编号/`home` 出身房间/`tint` 色调/`role` |
| `assets/map/jishilou_floor3/_region_preview.txt` | 提取器产物 | ASCII 区域预览图，改 overrides 时对照用 |

### 6.4 地图提取管线（tools/）
```bash
# 地图数据再提取（地图 .tscn 或 room_overrides.json 改动后执行，两个参数同为地图目录）
python frontend/tools/extract_map_data.py frontend/assets/map/jishilou_floor3 frontend/assets/map/jishilou_floor3
# 角色帧动画生成（4 套 char_N.tres；帧布局取自 AI-Town，一般不需要重跑）
python frontend/tools/gen_char_frames.py <AI-Town角色贴图目录> frontend/scenes
```
extract_map_data.py 四步原理：① `wall_3` 图层=墙本体（初版按障碍邻接判门曾漏检 2 格宽
门导致 R01 吞全图，已废弃）；② 门=墙线上 **1~3 格宽缺口**且两侧必须是真墙（家具缝隙
不误封）；③ 门格临时封闭后洪水填充 → 连通块=区域（<8 格碎片丢弃）；④ 孤立格 BFS
归并到相邻区域。

### 6.5 关键设计取舍
1. **区域判定两层设计**：纯几何无法判语义（哪块是"机房"哪块是"走廊"）→ 自动层
   （几何提取，保证房间闭合、不穿墙）+ 语义层（`room_overrides.json` 虚拟墙+改名）。
   **房间名可随便改**：前后端逻辑只认 R01~R13 编号，renames 只是显示层。
2. **视野规则按区域类型区分**（M2.3 上报的判定基础）：`room`=室内互见全部；
   `corridor`=距离截断（约 10 格，"走廊里看不见走廊尽头"）。
3. **后端指令接口现在就定型**：`npc.go_to_room(rid)` 与后端未来 `/world/state` 的
   "目标区域"字段一一对应（区域 id 同源 R01~R13），M2.2 接入时前端零改动。
4. **贴图复用+程序生成动画**：10 NPC 复用 AI-Town 的 4 套 48x70 角色图（idle 14 帧+
   四向 walk 各 6 帧，5fps），以 `modulate` 色调+名牌区分；38 帧动画数据由
   gen_char_frames.py 按帧布局生成 .tres，不手工拼 SpriteFrames。

### 6.6 当前 13 区域一览（名字待定，改法见 §6.3 overrides）
| id | 名称 | 类型 | 面积(格) | 备注 |
|---|---|---|---|---|
| R01 | 走廊与开放区 | corridor | 802 | 横贯走廊，长宽比 8.4 |
| R02 | 机房B(待定) | room | — | 上排左 |
| R03 | J3实验室(待定) | room | — | 上排中 |
| R04 | 右侧楼道(竖向通道) | corridor | — | 右侧竖向楼道 |
| R05 | 机房C(待定) | room | — | 上排右 |
| R06 | 左下开放区(待定) | room | 177 | |
| R07 | 茶水间(待定) | room | 108 | |
| R08/R11/R13 | 楼梯间南段/北段/西口(待定) | room | 51/25/10 | 楼梯瓦片被上层覆盖未识别为 stairwell，类型可直接改 |
| R09 | 中部小间(待定) | room | 48 | |
| R10 | 楼道门厅(待定) | room | 28 | |
| R12 | 小隔间(待定) | room | 16 | |

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
4. 验收：底部**"输出"面板**应打印 `[Game] 网格 133x70 房间 13 个` →
   `[Main] 已生成 10 个 NPC`，随后每 4~7 秒 `[日程] ...` / `[到达] ...`。

### 7.3 操作与验收点
| 按键 | 功能 |
|---|---|
| 滚轮 | 缩放（0.8~5 倍） |
| **方向键** | 镜头平移（越放大移得越慢；**WASD 保留给 M4 玩家实体**） |
| 空格 | 循环跟随 10 个 NPC（左上角显示跟随对象） |
| Esc | 取消跟随 |
| **F3 或 V** | 调试视图：13 个区域框+名称+类型、门口黄块标记、黄色寻路目标线（部分笔记本 F 键默认是多媒体键，按 Fn+F3 或直接按 V） |
| **快进1小时 / 快进4小时**（右下角按钮） | 游戏内时间控制：后端时钟跳进 → 日程翻段 → NPC 迁移（仅后端在线时可用；离线模式自动隐藏） |

核心验收点：NPC **绕墙不穿模**、过门走门洞、F3 区域框与视觉房间吻合、跟随平滑。

### 7.4 命令行方式（无窗口冒烟测试，改代码后快速回归）
```powershell
# 跑 600 帧(60fps≈10 秒)自动退出; stdout/stderr 落文件再看, 控制台直显可能截断
& "E:\...\Godot_v4.5.2-stable_win64.exe" --headless --path "E:\...\Roamchestra\frontend" --quit-after 600 > out.txt 2> err.txt
```
- `--headless` 无窗口跑完整游戏逻辑（JSON 加载/NPC 生成/寻路照常）；
- 验收标准：`err.txt` 为空，`out.txt` 含 §7.2 第 4 步的三类日志；
- 其他常用参数：`--editor`（直接开编辑器）、`--path <目录>`（指定工程）。

### 7.5 Godot 常见问题（另见 §13 排查表）
| 症状 | 处理 |
|---|---|
| 弹"将修改为 Godot 4.7" | 正常升级提示，点确认（§7.1） |
| uid 警告 / "uid not found" | 网页绘图工具导出的场景带自制 uid 字符串，无害；Godot 按路径解析，编辑器保存一次后自动生成正确缓存 |
| F5 提示 "No main scene defined" | 文件面板右键 `scenes/main.tscn` → 设为主场景 |
| 中文显示方块 | Godot 4 自动回退系统字体（微软雅黑），正常 Windows 不出现 |
| NPC 走在桌子/柜子图块上 | 家具碰撞只挡部分层；观感不佳可在 `room_overrides.json` 加虚拟墙后重跑提取器（§6.4） |
| 改了区域名没生效 | 改 `room_overrides.json` 的 `renames`（不是 rooms.json，它是产物会被覆盖）→ 重跑提取器 |

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
| GET | `/world` | 世界快照（时间/全员位置/最近事件）；M2.2 起每个 NPC 含 `grid`/`target_grid`（前端寻路目标，语义区域→R## 投影） |
| GET | `/npcs` | 角色名单（身份/性格/位置） |
| GET | `/npcs/{name}/memory` | ★个人记忆面板: `active`/`forgotten`/`reflections` 三视图 |
| GET | `/relations` | 全员关系矩阵（0~100，None=陌生人） |
| GET | `/areas` | 区域地图 |
| POST | `/conversation` | ★触发 NPC↔NPC 对话 `{a, b, trigger}` |
| POST | `/world/advance` | ★时间推进+遗忘+反思 `{minutes}`（快进3天=4320） |
| POST | `/world/goto` | 指派角色前往某区域 `{npc, area}`：立即就位（M1 语义不变）+ 前端跟随，日程翻段后自动失效 |
| POST | `/world/report` | 前端到达上报 `{npc, grid}`：只更新位置+日志，**不写记忆**（防移动噪音；同室观察记忆留给 M2.3） |
| POST | `/chat` | 玩家↔NPC 对话 `{npc, message}`（M4 扩展基础） |

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

### 11.3 为什么 NPC 位置是"逻辑概念"？
移动是前端（Godot）的事。后端只认 `npc_area` 字典；M1 用 `set_area()` 传送 +
`co_located_pairs()` 相遇判定。M3 调度器只需要在这个判定上加循环即可，
前端接入后把 `goto` 换成接收移动事件。

### 11.4 离线兜底（答辩保险）
LLM 挂了/没 key 时: 说话 → 用人格卡 example_lines 随机兜底；
记忆 → 模板内容（importance 0.4）；关系 → 固定 +1。**演示永不中断**，
只是内容质量下降。判断依据: `llm.chat()` 返回 None。

### 11.5 依赖为什么只有 5 个包？
fastapi/uvicorn（接口）、openai（SDK）、pydantic（校验）、python-dotenv（配置）。
向量检索纯 Python 余弦（记忆条目 <10^3，无性能问题），不引入 numpy/torch。

## 12. 扩展指南（接手后往哪加）

1. **加角色**: personas/ 加 JSON → 完成（关系/日程/秘密全在卡里）
2. **加区域**: 后端 `world.py` `AREAS` 加一项；前端则改 `room_overrides.json`
   （虚拟墙/改名）后重跑提取器，流程见 §6.4，区域一览见 §6.6
3. **加场景**: scripts/run_demo.py 照抄一个"场景块"（goto + inject_event +
   run_conversation）；或运行时 `POST /conversation`
4. **调遗忘速度**: config.py `MEMORY.half_life_minutes`（默认 300，越小忘越快）、
   `forget_threshold`（越大越容易忘）、`capacity`（越小淘汰越狠）
5. **M3 调度器**: 新建 scheduler.py，循环: `world.co_located_pairs()` →
   按"日程/目标/关系"挑一对 → 调 `service.run_conversation` → `advance_time`
6. **玩家模式 M4**: 现有 `POST /chat` 是最小实现；扩展成玩家加入世界
   （persona 生成 + 关系初始化 + NPC 可主动找玩家 = 中断机制，届时用
   LangGraph `interrupt()`）
7. ✅ **前端接入（M2.2 已完成）**: `frontend/scripts/api_client.gd`（autoload）每秒
   GET `/world` 取各 NPC 的 `target_grid` → 与 `npc.goal_room` 不同才调
   `go_to_room()`（不打断行走中的人）；到达 POST `/world/report` 上报**真实位置**
   （只更新事实不写记忆，已确认决策）；连续 3 次失败自动回退本地演示日程，
   后端恢复后自动切回。后端侧：`world.py` 加 `GRID_OF` 映射表（语义区域→R##，
   ★后续同学只改这张表★）+ 惰性 `target_area()`（显式指派 > 日程，指派随日程
   翻段自动失效）+ `report_arrival()`（几何 id 反查语义 id 去抖）。
   `/npcs/{name}/memory` 拉取与 WebSocket（`/ws/events`）在 M3 面板阶段再加
8. **前端地图/动画再生成**: 见 §6.4 管线命令；改完用 §7.4 的命令行冒烟测试回归

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
| 想重置世界 | 删 `data/memory.db` 或用 `GameService(fresh_db=True)`（api/run_demo 默认已如此） |
| Godot 打不开工程/弹升级提示 | 引擎版本与排错见 §7.5（"将修改为 Godot 4.7"弹窗点确认即可） |
| Godot 运行报 SCRIPT ERROR | 看错误行的脚本名+行号；若是资源路径问题先删 `frontend/.godot/` 缓存重新导入 |
| 前端看不到任何 NPC | 查 Output 面板是否打印 `[Main] 已生成 10 个 NPC`；没有则 `data/npc_roster.json` 路径/JSON 被改坏 |
| 前端"已连接后端"但 NPC 不挪窝 | 后端日程没翻段：`target_grid` 按人格卡 `schedule` 时刻表**惰性**计算，时间不跨段目标不变（前端 WANDER 在房内闲逛是正常的）；可 `POST /world/advance` 快进触发翻段 |
| room_id_at 等按数字键查字典查不到 | JSON 解析出的数字是 **float**，GDScript 字典里 `3.0` 和 `3` 是不同键——跨 JSON/代码的数字键必须显式 `int()`（M2.2 实际踩坑，详见 game_data.gd `room_id_at` 注释） |


