# 连续快进、原版对话节奏与试走转场

更新：2026-11-14。本说明按最终确认的需求维护：恢复原版后端速度与对话显示节奏，保留连续快进，气泡不限制人物行为，并统一试走和 NPC 的跨馆出口。

## 使用方式

- 默认 **×1**：恢复原版后端的现实 1 秒推进 10 游戏分钟；前端同步该基准。离线演示也使用该基准。
- 主界面保留“快进1小时 / 快进4小时”，社交观察面板保留“快进3天”。倍速选择支持原速的 ×1 / ×2 / ×4 / ×6。
- 倍速同时作用于游戏时钟、NPC 行走、闲逛等待及角色动画。人物保持原路径逐格移动，不直接传送。
- “暂停 / 继续世界”同步暂停时钟与人物移动；继续时保留原倍率。
- 对话恢复原版节奏：新台词立即替换当前文字，不排队；长台词按最多 60 字分段，每段最多显示 10 现实秒，新台词可提前替换。模型生成速度未作人为延迟。
- 气泡只是观察显示，不占用角色、不延迟日程或下一场社交。实际模型任务仍保留原有的并发互斥，处理完成即可恢复行为。
- 日志仍记录完整台词。重连只恢复当前最新一句尚在显示的段落，旧台词仅补入日志。
- 试走模式支持“智信馆二楼西端楼梯口 ↔ 济事楼三楼西侧楼梯口”。三楼西楼梯提供“上四楼 / 前往智信馆”的选择，原有东西楼梯的三楼↔四楼通行保留。

## 实现与接口

后端参数集中在 `backend/config.py`：`SOCIAL.speed=10.0` 是原速，`fast_forward_speed=60.0` 是定时快进速度，`bubble_seconds=10.0`、`bubble_segment_chars=60` 控制原版气泡显示。

接口里的 `speed` 单位是“游戏分钟/现实秒”，不是界面倍率。`base_speed=10` 表示原速基准，因此 `speed=40` 对应移动和时钟 ×4，`speed=60` 对应 ×6；前端人物原速仍为每秒 55 像素，不会因为恢复 10 分钟基准而默认跑快 10 倍。

时钟使用现实单调时间计量。切换倍率、暂停或继续之前，先以旧状态结算已经经过的时间，避免用新倍率回算上一个 tick。`GET /world` 新增数值 `minutes`；前端在快照之间连续计时，平滑修正误差，旧快照不会把显示时间向后跳。后端原有时间字符串保留兼容，主界面显示游戏秒数和当前倍率。

`POST /world/speed` 和 `POST /social/control` 支持部分更新：

```json
{"speed": 40}
```

```json
{"paused": true}
```

未提供的字段保持原值。选择界面中的倍速会同时恢复世界运行。手动改变倍率会取消正在进行的定时快进。

`POST /world/advance` 改为**启动连续快进**，不再立即跳时。例如：

```json
{"minutes": 4320, "speed": 60}
```

表示从当前时间开始，连续推进 4320 游戏分钟，约需 72 现实秒；抵达三天后的目标时刻后恢复先前速度。1 小时 / 4 小时按钮同样连续运行，约需 1 秒 / 4 秒。返回 `target_minutes`、`speed`、`paused` 和当前时间；请求返回不表示已经完成。暂停保留目标，手动切换倍率取消目标。

遗忘与反思仍由正常世界循环触发；抵达目标时额外标记一次最终维护，即使已有维护任务未结束，也会在空闲后补做。内部 `GameService.advance_time()` 和 CLI 演示保留直接推进，正式 HTTP / 游戏界面不调用跳时路径。

后端 `backend/playback.py` 生成气泡分段，通过 `dialogue_line.segments` 下发；前端收到后即时替换。显示生命周期与 `busy` 完全分离，`finish()` 处理完成就释放角色并结束会面。WebSocket 快照只携带各场对话最新一句及其 `playback_elapsed`；过期显示数据独立清理。

地图出口在 `game_data.gd` 的 `stair_entry()` / `stair_destinations()` 统一定义，NPC 路线和 `map_viewer.gd` 的试走交互共同读取，避免两种模式出现不同的落脚点。跨馆不改地图图层，也不覆盖三楼西楼梯原来的上楼出口。

## 回归验证

在仓库根目录运行，Godot 路径可按本机实际位置调整：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -p test_social.py -v
& '..\tools\godot\Godot_v4.5.2-stable_win64_console.exe' --headless --path frontend --script "$PWD\tests\check_social_ui.gd" --quit-after 180
& '..\tools\godot\Godot_v4.5.2-stable_win64_console.exe' --headless --path frontend --script "$PWD\tests\check_time_flow.gd"
& '..\tools\godot\Godot_v4.5.2-stable_win64_console.exe' --headless --path frontend --script "$PWD\tests\check_trial_transitions.gd" --quit-after 240
.\.venv\Scripts\python.exe -X utf8 -B tests\run_websocket_smoke.py
& '..\tools\godot\Godot_v4.5.2-stable_win64_console.exe' --headless --fixed-fps 60 --path frontend --script "$PWD\scripts\check_campus_navigation.gd"
$env:ROAM_TEST_SPEED='6'
& '..\tools\godot\Godot_v4.5.2-stable_win64_console.exe' --headless --fixed-fps 60 --path frontend --script "$PWD\scripts\check_campus_navigation.gd"
Remove-Item Env:ROAM_TEST_SPEED
```

本轮覆盖：10 分钟原速、连续计时、状态切换前结算、三天目标和原速恢复、暂停 / 取消、延后的最终遗忘维护、原版即时替换和10秒显示、去重 / 重连、气泡不阻止后续社交或实际移动、试走跨馆双向转场，以及原速 / ×6 的校园导航和门扇回归。

本机结果：25 项后端测试全部通过；Godot 对话界面、时钟与实际移动倍率、试走转场、真实 TCP WebSocket、原速和 ×6 校园导航均通过。模型测试使用模拟模型，不代表真实模型生成质量的验收。

运行旧后端进程或已经打开的游戏实例不会自动加载全部修改；更新后需要重启后端并重新运行游戏。
