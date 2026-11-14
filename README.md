# Roamchestra 🏫 — 校园多智能体交互系统

以 **NPC↔NPC 自主交互**为核心的校园多智能体课程大作业系统。
场景设定在同济大学校园（济事楼三楼、四楼与智信馆二楼），7 名学生 + 3 名导师/教务
NPC 在里面自主对话、积累记忆、互相遗忘、发展关系。

> 本项目基于 Helloagents-AI-Town 的问题意识重新实现：
> 基座的"NPC 对话"是互不相关的批量独白；这里让 NPC 真正感知彼此、
> 双向对话、把经历写进自己的记忆，并随时间**可视化地遗忘**。

## 核心特性（已实现）

- 🎭 **人格卡驱动**：每个角色一张 JSON 卡（性格/目标/秘密/日程），改卡即改人
- 💬 **真·双向对话**：发起 → 多轮往返 → 双方以各自视角沉淀记忆
- 🧠 **自研记忆系统**：向量检索（新近度×重要性×相关度三因子打分）
- 🫥 **可视化遗忘**：时间衰减 / 容量淘汰 / 反思压缩三机制，全部留痕可查询
- ❤️ **关系网络**：每次对话后 LLM 评估好感度变化（带原因）
- ⏩ **连续快进**：保留原版后端现实 1 秒 = 游戏 10 分钟；1小时/4小时/3天快进连续运行、自动恢复原速，时钟、移动与动画同步加速。对话恢复原版即时替换，气泡不占用人物
- 🗺️ **校园寻路**：10 个 NPC 在三张楼层图的 40 个区域间活动，按实际地板和障碍物寻路。运行 Godot 主场景，按 1、2、3 切换观察楼层，空格跟随 NPC，V 查看分区和当前行走目标。

## 快速开始

```bash
cd Roamchestra
pip install -r requirements.txt
copy .env.example .env       # 填入你的 API key
python scripts/run_demo.py   # 命令行演示（答辩入口）
uvicorn backend.api:app --port 8000   # HTTP 接口（文档在 /docs）

# 5) 跑 Godot 前端（M2.2，可独立运行，也可接后端）
#    ① 下载 Godot 4.5.2 stable 标准版（不是 .NET 版）: godotengine.org/download/archive/
#    ② 双击 exe（绿色免安装）→ 项目管理器选"导入" → frontend/project.godot
#    ③ 编辑器里按 F5 运行（详细说明与排错见 docs/CODE_GUIDE.md §7）
#    ④ 双端联调: 终端 A 先起 uvicorn backend.api:app --port 8000，再 F5 →
#       左上角显示"后端驱动" = NPC 按后端日程移动；
#       后端不在线时前端自动回退本地演示日程，永不白屏
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/CODE_GUIDE.md](docs/CODE_GUIDE.md) | **交接必读**：代码结构/机制详解/扩展指南 |
| [docs/M3.md](docs/M3.md) | NPC调度、LangGraph对话、事件流与社交观察基础 |
| [docs/M4.1.md](docs/M4.1.md) | 时间/观察修正、公开资料、动态日程、公告栏、玩家会话 |
| [校园地图改图交接](map_sources/README.md) | 三份可编辑源图、障碍与装饰划分、在线改图及 Godot 重建步骤 |
| [docs/MAP_SPEC.md](docs/MAP_SPEC.md) | 地图格式、图层约定和早期实现记录 |
| [docs/DESIGN.md](docs/DESIGN.md) | 需求与设计方案（FR/架构/里程碑） |
| [personas/](personas/) | 10 张人格卡（7 学生 + 3 导师教务） |

## 技术栈

Python 3.11 · FastAPI · SQLite（记忆/关系持久化） · 同济 AGI 网关
（GLM-5.3-Flash 对话 / GLM-5.3 深思 / Qwen3-Embedding 向量，OpenAI 兼容协议）
· Godot 4.5.2 stable（前端引擎，绿色版免安装，仅运行前端需要）

## 路线图

- [x] M1: 后端完整版 —— 人格卡 + NPC↔NPC 对话 + 三因子记忆检索 + 遗忘三机制 + 关系矩阵 + HTTP API
- [x] M2.1: Godot 前端独立运行 —— 济事楼三楼初版地图分区/ A* 寻路 / 10 NPC 日程移动 / F3 调试视图
- [x] M2.2: 前端↔后端联动 —— `/world` 驱动 NPC 移动 + 到达位置上报 + 断线自动回退本地日程（当前）
- [x] M3: 调度器（相遇自动触发对话）+ 八卦传播 + 对话气泡/记忆面板/关系面板 UI
- [x] M4.1：公开资料与动态日程 + 公告栏功能 + 默认关闭的玩家入口（现仅支持文字交互）
- [ ] M4.2：完整玩家模式（默认关闭，interrupt 开关）+ 跨楼层 + 完整演示打磨

