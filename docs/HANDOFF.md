# PixelTJCS 项目交接

## 当前交接 | M6（2026-11-22）

- **代码位置**。M6 在本地 `better-interaction` 分支的工作区开发，基于 `main` 的 `8a2758a`。截至本次整理，M6 改动尚未提交或合并。交付时一并核对已修改和新增的源码、资源与测试。
- **完成范围**。统一事件记录与三档调度、实际位置追踪与邀约、公告和旁听记忆、角色状态栏和时间线、上帝模式、双击启动。各模块入口和数据流见 [M6 交接说明](M6.md)。
- **启动和验证**。Windows 上可用仓库根目录的 `Start-PixelTJCS.bat`，先启动后端，再打开 Godot。全量后端 148 项与 Godot 社交界面、公告联调已通过。模型分支由替身覆盖，真实模型长时间试玩待验收。 [启动与验收步骤](M6.md#5-启动和验收)
- **接手顺序**。先用临时存档跑通找人、旁听、退场再出演；再用真实模型观察活动结果、邀约成功率和记忆保留。赴约时间估算、正式八卦候选的入口缺口见 [待接手事项](M6.md#6-已知边界与待接手事项)。

## 历史交接 | 2026-11-17

本文记录本机 `D:\MyCode\Roamchestra` 的实际工作状态。功能细节见 [代码指南](CODE_GUIDE.md)、[M3 社交机制](M3.md)、[M4.2 玩家模式](M4.2.md)；历史里程碑文档中的“待完成”字样可能指当时的状态。

### 当时的状态

- **代码**：M1～M4.2 的 README 路线图实现项已完成；`main` 分支工作区仍有修改和新增文件，**尚未提交或推送到 GitHub**。交接或部署前先审阅 `git diff`、新增文件及 Godot 生成的 `.import` 变动。
- **运行**：截至本次交接，Godot 游戏和 `uvicorn backend.api:app` 均已关闭，避免自主 NPC 对话继续消耗模型 token。最后一次停机前保存了世界时间和 NPC 所在区域。
- **模型**：本机 `.env` 已配置同济 AGI 网关，曾用短请求收到真实模型回复。`.env` 和 `data/memory.db` 均被 Git 忽略；不要把密钥、玩家凭据或数据库提交到仓库。

### 已完成的工作

| 模块 | 已实现内容 | 主要入口 |
| --- | --- | --- |
| M1 后端基础 | 10 张人格卡、NPC 双向多轮对话、记忆检索与遗忘、关系评估、SQLite 持久化和 HTTP API | `backend/service.py`、`backend/conversation.py`、`backend/memory.py`、`personas/` |
| M2 前端与联动 | Godot 三张楼层地图、NPC 寻路与跨层移动；`/world` 下发行程、`/world/report` 上报实际位置；后端断开时回退本地 NPC 演示 | `frontend/project.godot`、`frontend/scripts/main.gd`、`frontend/scripts/api_client.gd` |
| M3 自主社交 | 相遇和主动拜访、会面队列、LangGraph 调度、八卦、WebSocket 台词事件、对话气泡、记忆与关系面板 | `backend/scheduler.py`、`backend/social.py`、`frontend/scripts/social_ui.gd` |
| M4.1 扩展 | 公开资料、动态日程、公告栏、连续快进、默认关闭的玩家身份和会话、玩家与 NPC 的文字聊天及记忆/关系保存 | `backend/daily_planner.py`、`backend/noticeboards.py`、`backend/player_mode.py` |
| M4.2 玩家前端 | 可行走玩家、楼梯换层、靠近按 E 交谈、游戏内机制说明；交谈期间 NPC 停下，收起面板后恢复行程 | `frontend/scripts/player_avatar.gd`、`frontend/scripts/player_chat.gd`、`frontend/scripts/mechanics_panel.gd` |

近期修复还包括：

- **聊天距离与占用**：后端依据双方最近 2 秒上报的同房间坐标验证 96 像素距离；玩家交谈使用 45 秒租约、前端每 10 秒续约。其他 NPC 不能同时占用交谈对象。发送按钮旁会显示未满足的条件。
- **会面等待**：角色忙碌或执行手动任务时，会面最多等待 300 现实秒；超时退出队列，未超时时在占用释放后继续。
- **气泡离场同步**：NPC↔NPC 最后一句最多再停留 3 现实秒；服务端先发 `dialogue_finished` 清除地图气泡，再释放 NPC。玩家↔NPC 在收起面板、换人或租约到期时清气泡；若一轮仍在生成，完成保存后再释放。完整台词仍在记录中，重连不会恢复已结束的气泡。
- **运行数据控制**：事件历史、记忆快照有上限；无变化的记忆不重复生成快照，暂停世界不持续写相同快照；时钟、NPC 房间和对话冷却可在重启后恢复。

### 验证记录

- 2026-11-17：后端全量 `unittest` **97 项通过**。自动测试使用模拟模型，不调用真实网关。
- Godot headless 的 `tests/check_social_ui.gd` 和 `frontend/tests/check_player_mode.gd` 通过，覆盖气泡结束事件、并发与重连，以及玩家实体和近距离入口。
- 真实网关短请求曾成功；用户在可见窗口观察最近的聊天与气泡离场后反馈“没啥问题了”。这不等同于完成下面的整套人工验收清单。

### 当时尚未完成或待确认

1. **M4.2 完整可见窗口验收仍未记录完成**：逐项检查三层楼梯往返、行走手感、远处角色不能发送、靠近按 E 发送及回复、收起后 NPC 恢复行动、离开并重新加入后历史仍在、断开后端时本地演示正常。记录结果后再更新 README 的复选框。
2. **代码交付**：审阅当前未提交的代码、测试、文档与 Godot `.import` 文件；确认要纳入的文件，再提交和推送。当前本机修改不在远程仓库上。
3. **可选功能**：玩家主动中断正在进行的 NPC↔NPC 对话开关未实现，不影响核心演示。多人聊天已决定暂缓，当前所有轮次仍为双人对话。
4. **部署边界**：服务端距离规则依赖客户端上报坐标，适合当前本地演示；若未来允许不受信任的远程客户端，需要服务端接管移动和地图碰撞判定，才能防止伪造位置。

### 启动、测试与停机

在 PowerShell 中从仓库根目录运行：

```powershell
cd D:\MyCode\Roamchestra
.\.venv\Scripts\python.exe -X utf8 -B -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
# 在另一个 PowerShell 窗口启动可见游戏
& 'D:\demo\tools\godot-4.5.2\Godot_v4.5.2-stable_win64.exe' --path 'D:\MyCode\Roamchestra\frontend'
```

启动后左上角出现“后端驱动”才表示双端已连接。玩家先点“玩家模式”并加入，再走到 NPC 同一房间的 6 格内按 E；看到“对方已停下”后输入消息。后端未连接时，NPC 地图可本地演示，但玩家不能新加入或发送。**后端运行期间会自主触发模型调用**，试玩结束用 `Ctrl+C` 停止后端并关闭 Godot。

离线自动验证：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -q
& 'D:\demo\tools\godot-4.5.2\Godot_v4.5.2-stable_win64_console.exe' --headless --path frontend --script 'res://../tests/check_social_ui.gd'
& 'D:\demo\tools\godot-4.5.2\Godot_v4.5.2-stable_win64_console.exe' --headless --path frontend --script 'res://tests/check_player_mode.gd'
```

建议下一步先完成可见窗口验收并记录结果，再审阅差异、提交代码。可选功能留到核心交付之后。
