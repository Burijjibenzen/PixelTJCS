# Roamchestra 🏫 — 校园多智能体交互系统

以 **NPC↔NPC 自主交互**为核心的校园多智能体课程大作业系统。
场景设定在同济大学校园（纪事楼 3F/4F、智信馆 4F），7 名学生 + 3 名导师/教务
NPC 在里面自主对话、积累记忆、互相遗忘、发展关系。

> 本项目基于 Helloagents-AI-Town 的问题意识重新实现：
> 基座的"NPC 对话"是互不相关的批量独白；这里让 NPC 真正感知彼此、
> 双向对话、把经历写进自己的记忆，并随时间**可视化地遗忘**。

## 核心特性（M1 + M2.1 已实现）

- 🎭 **人格卡驱动**：每个角色一张 JSON 卡（性格/目标/秘密/日程），改卡即改人
- 💬 **真·双向对话**：发起 → 多轮往返 → 双方以各自视角沉淀记忆
- 🧠 **自研记忆系统**：向量检索（新近度×重要性×相关度三因子打分）
- 🫥 **可视化遗忘**：时间衰减 / 容量淘汰 / 反思压缩三机制，全部留痕可查询
- ❤️ **关系网络**：每次对话后 LLM 评估好感度变化（带原因）
- ⏩ **时间加速**：游戏时钟与遗忘半衰期联动，一次快进 3 天看记忆褪色
- 🗺️ **2D 像素校园（M2.1）**：纪事楼 3F 真实布局自动提取出 13 个功能区，NPC 走 A* 寻路 + 日程移动，Godot 4.5 独立运行（详见 docs/CODE_GUIDE.md §6/§7）

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
- [x] M2.1: Godot 前端独立运行 —— 纪事楼 3F 地图分区（13 区域）/ A* 寻路 / 10 NPC 日程移动 / F3 调试视图
- [x] M2.2: 前端↔后端联动 —— `/world` 驱动 NPC 移动 + 到达位置上报 + 断线自动回退本地日程（当前）
- [ ] M3: 调度器（相遇自动触发对话）+ 八卦传播 + 对话气泡/记忆面板/关系面板 UI
- [ ] M4: 玩家模式（默认关闭，interrupt 开关）+ 跨楼层 + 完整演示打磨

