# PixelTJCS · 同济校园多智能体模拟

PixelTJCS 是一个以同济大学济事楼和智信馆为场景的像素风校园模拟项目。7 名学生、2 名导师和 1 名教务 NPC 会按各自的日程在校园里活动，会与遇到的同学交谈，也会阅读公告，并从这些经历中形成记忆、发展关系。角色的位置、当前活动和事件时间线都可以在游戏里查看。

Ubuntu 网页版支持多人同时旁观。同一时间可有一位玩家以「同小济」的身份加入校园，走近 NPC 交谈，或通过上帝模式发布公告、指派行动。玩家交谈期间，其他 NPC 可以在附近等候，交谈结束后再发起邀约。

[GitHub 仓库](https://github.com/Burijjibenzen/PixelTJCS) · [Ubuntu 部署分支](https://github.com/Burijjibenzen/PixelTJCS/tree/Ubuntu-Released)

> 本项目基于 Helloagents-AI-Town 的问题意识重新实现：
> 基座的"NPC 对话"是互不相关的批量独白；这里让 NPC 真正感知彼此、
> 双向对话、把经历写进自己的记忆，并随时间**可视化地遗忘**。

## 核心特性（已实现）

- 🎭 **人格卡驱动**：每个角色一张 JSON 卡（性格/目标/秘密/日程），改卡即改人
- 💬 **真·双向对话**：发起 → 多轮往返 → 双方以各自视角沉淀记忆
- 🧠 **自研记忆系统**：向量检索（新近度×重要性×相关度三因子打分）
- 🫥 **可视化遗忘**：时间衰减 / 容量淘汰 / 反思压缩三机制，全部留痕可查询
- ❤️ **关系网络**：每次对话后 LLM 评估好感度变化（带原因）
- ⏩ **连续快进**：原速为现实 1 秒对应游戏 10 分钟。支持 ×1／×2／×4／×6 和定时快进 1 小时、4 小时或 3 天。定时快进结束后恢复原倍率，时钟、移动与动画同步加速。
- 🗺️ **校园寻路**：10 个 NPC 在三张楼层图的 40 个区域间活动，按实际地板和障碍物寻路。运行 Godot 主场景，按 1、2、3 切换观察楼层，空格跟随 NPC，V 查看分区和当前行走目标。
- 🚶 **可行走玩家**。加入后用 WASD 或方向键行走。靠近 NPC、告示栏或楼梯时按 E 交互，P 将镜头移回玩家。对话框内按 Enter 发送，Shift + Enter 换行。

### M6 交互与事件系统

- **角色状态栏与事件时间线**。查看每位角色的位置和当前活动，悬停临时跟随，点击定位并打开资料。事件时间线保存实际经过、角色判断、后续动作和调度原因。
- **事件驱动的自主行动**。模型可根据活动结果或对话决定找人、去某地做事、延长活动或继续原安排。调度按基础日程、自主响应和玩家指派三档处理。找人追踪实际位置，双方到位后发起邀约，对话期间站定并按顺序显示气泡。
- **公告与旁听记忆**。NPC 实际读完公告后形成个人记忆。在同一房间或走廊内，距说话者 150 地图像素以内的旁听者可以记住听到的片段。角色回忆可以带有个人推测，事件保留实际台词和公告原文。
- **上帝模式**。集中发布及删除公告、查看历史、用悄悄话指派行动、控制 NPC 退场或出演，以及确认后重置世界。 [M6 功能与代码交接](docs/M6.md)

## 快速开始

### Ubuntu 网页版

在已准备好 Python 3.11 虚拟环境、Qwen3.5-9B 权重和 Web 导出文件的服务器上，从项目根目录依次运行。模型状态显示就绪后再启动 Web。

```bash
bash scripts/serve_qwen.sh
bash scripts/serve_qwen.sh status
bash scripts/serve_web.sh
bash scripts/serve_web.sh status
```

能连接到演示服务器的电脑可直接在浏览器打开 [PixelTJCS 网页预览](http://100.64.162.146:8765/http-preview/)。服务默认在后台运行。两条脚本都支持 `logs` 查看实时日志和 `stop` 停止服务。

默认由本地 Qwen3.5-9B 处理 NPC 自主活动。在 `.env` 配置网关密钥 `LLM_API_KEY` 后，玩家对话优先调用 GLM-5.3-Flash，API 不可用时回退到本地模型。全程使用本地模型可通过 `PIXELTJCS_LLM=local bash scripts/serve_web.sh` 启动。

首次部署需安装 Python 依赖、准备模型环境，并提前导出 `build/web/` 下的备用首页。可用 `QWEN_PYTHON` 和 `QWEN_MODEL_DIR` 指定本地模型环境和权重路径。前端更新后运行 `bash scripts/build_http_preview.sh` 重新导出 `/http-preview/`。完整的导出及备用访问方式见 [Ubuntu Web 部署交接](docs/UBUNTU_WEB_HANDOFF.md)。

### Windows 桌面版

Windows 上可双击根目录的 `Start-PixelTJCS.bat`。首次使用时，将 `launcher.local.bat.example` 复制为 `launcher.local.bat`，把 `PIXELTJCS_PYTHON` 指向当前 Python 或 Conda 环境的 `python.exe`，把 `GODOT_EXE` 指向 Godot 可执行文件。此本机配置不会提交到 Git。若已激活 Conda 环境或项目内有 `.venv`，Python 路径可以不填；若 Godot 在 PATH 中，Godot 路径也可以不填。脚本先启动后端，健康检查通过后打开 Godot，关闭游戏窗口后停止本次启动的后端。

前端默认连接 `http://127.0.0.1:8000`。后端在另一地址时，运行 Godot 前设置 `PIXELTJCS_BACKEND_URL`（如 `http://服务器地址:8000`）；HTTP 和 WebSocket 会使用同一地址。

`scripts/run_demo.py` 是单独的命令行演示，会初始化运行数据库。带存档的日常启动使用双击脚本或手动启动双端。

Windows PowerShell 建议用 Python 3.11 的独立环境安装；若系统默认 GBK 编码，`-X utf8` 可正常读取依赖文件中的中文注释：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

手动联调时，在 Godot 4.5.2 中打开 `frontend/project.godot`，按 F5 运行。桌面前端可独立播放本地 NPC 日程；玩家会话和模型对话需要连接后端。模型网关配置可以从 [`.env.example`](.env.example) 复制到本地 `.env`。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/CODE_GUIDE.md](docs/CODE_GUIDE.md) | **交接必读**：代码结构/机制详解/扩展指南 |
| [docs/M3.md](docs/M3.md) | NPC调度、LangGraph对话、事件流与社交观察基础 |
| [docs/M4.1.md](docs/M4.1.md) | 时间/观察修正、公开资料、动态日程、公告栏、玩家会话 |
| [docs/M4.2.md](docs/M4.2.md) | 玩家行走与近距离交互、运行边界、验收记录 |
| [docs/M6.md](docs/M6.md) | M6 的事件与调度、社交和记忆、上帝模式、验证与接手事项 |
| [docs/UBUNTU_WEB_HANDOFF.md](docs/UBUNTU_WEB_HANDOFF.md) | Ubuntu 后端与 Godot Web 前端迁移交接 |
| [docs/HANDOFF.md](docs/HANDOFF.md) | 当前完成状态、待验收事项、启动和交付步骤 |
| [校园地图改图交接](map_sources/README.md) | 三份可编辑源图、障碍与装饰划分、在线改图及 Godot 重建步骤 |
| [docs/MAP_SPEC.md](docs/MAP_SPEC.md) | 地图格式、图层约定和早期实现记录 |
| [docs/DESIGN.md](docs/DESIGN.md) | 需求与设计方案（FR/架构/里程碑） |
| [personas/](personas/) | 10 张当前人格卡（7 名学生、2 名导师和 1 名教务），旧卡归档在 `retired/` |

## 技术栈

Python 3.11 · FastAPI · LangGraph · SQLite · Godot。桌面开发使用 Godot 4.5.2 stable，HTTP 预览使用 Godot 4.8 beta 4 单线程导出。Web 前端、API 和 WebSocket 由同一个后端提供，模型调用兼容 OpenAI API。

## 路线图

- [x] M1: 后端完整版 —— 人格卡 + NPC↔NPC 对话 + 三因子记忆检索 + 遗忘三机制 + 关系矩阵 + HTTP API
- [x] M2.1: Godot 前端独立运行 —— 济事楼三楼初版地图分区/ A* 寻路 / 10 NPC 日程移动 / F3 调试视图
- [x] M2.2: 前端↔后端联动 —— `/world` 驱动 NPC 移动 + 到达位置上报 + 断线自动回退本地日程
- [x] M3: 调度器（相遇自动触发对话）+ 八卦传播 + 对话气泡/记忆面板/关系面板 UI
- [x] M4.1：公开资料与动态日程 + 公告栏功能 + 初版玩家文字交互入口
- [x] M4.2 实现：默认关闭的可行走玩家 + 近距离交互 + 跨楼层 + 游戏内机制说明 + 真实模型联通
- [ ] M4.2 可见窗口人工验收：行走手感、楼梯选择、近距离对话与离开重入
- [ ] 可选增强：玩家中断 NPC 对话的开关（不影响 M4.2 核心演示）
- [x] M6 实现：事件生命周期与角色时间线、三档调度、追踪找人与邀约、旁听和角色记忆、上帝模式、地图交互与双击启动
- [ ] M6 联调：真实模型长时间试玩，验证交谈完成率、记忆保留和旁听体验
- [x] Ubuntu 网页版支持内网 HTTP 访问、单玩家与多人旁观、本地推理和玩家对话的网关回退。
