extends Node2D
## M2.1 演示主场景: 生成 10 NPC
## M2.2 起双驱动: ApiClient 在线 = 后端日程驱动(本地日程停用);
##               离线  = 本地演示日程(断线兜底, 永不白屏)
## 决策已确认: 玩家模式默认关闭 (纯 NPC), M4 再加玩家; 镜头自由 + 空格跟随

const NPC_SCENE := preload("res://scenes/npc.tscn")
const ROSTER_PATH := "res://data/npc_roster.json"

var npcs: Array = []
var follow_idx := -1
var demo_clock := 8.0 * 3600.0    # 本地演示时钟(秒), 1 真实秒 = 60 游戏秒
var _sched_timer := 2.0
var _dbg_prev := false            # F3/V 轮询边沿检测(事件路由不可靠, 改查全局键态)
var _sched_ui_timer := 0.0        # 日程面板刷新节流

func _ready() -> void:
	Game.bind_room_layers($Map)
	var roster: Array = Game._load_json(ROSTER_PATH).get("npcs", [])
	for r in roster:
		var npc := NPC_SCENE.instantiate()
		$Actors.add_child(npc)
		npc.global_position = Game.nav_world_of(r.home)
		npc.setup(r.id, r.name, int(r.char), r.home,
			Color(r.tint[0], r.tint[1], r.tint[2]))
		npc.arrived.connect(_on_npc_arrived)
		npcs.append(npc)
	print("[Main] 已生成 %d 个 NPC" % npcs.size())
	$DebugDraw.setup(npcs)
	ApiClient.setup(npcs)
	ApiClient.online_changed.connect(_on_online_changed)
	$UI/BtnAdv1h.pressed.connect(func() -> void: ApiClient.advance_time(60))
	$UI/BtnAdv4h.pressed.connect(func() -> void: ApiClient.advance_time(240))
	ApiClient.advance_done.connect(_on_advance_done)
	($UI/SchedulePanel as PanelContainer).visible = false

func _process(delta: float) -> void:
	if ApiClient.backend_online:
		# 在线: 时钟与移动目标全部以后端为权威, 本地日程停用
		($UI/ClockLabel as Label).text = ApiClient.backend_time + " · 后端驱动"
	else:
		demo_clock += delta * 60.0
		($UI/ClockLabel as Label).text = _clock_str() + " · 本地演示时钟"
		_sched_timer -= delta
		if _sched_timer <= 0.0:
			_sched_timer = randf_range(4.0, 7.0)
			_random_schedule()
	# F3/V 切换区域调试视图: 轮询全局键态 + 边沿检测 (绕开事件路由/输入法)
	var dbg := Input.is_key_pressed(KEY_F3) or Input.is_key_pressed(KEY_V)
	if dbg and not _dbg_prev:
		_toggle_debug()
	_dbg_prev = dbg
	# 日程面板(后端在线时): 每 0.5s 刷新一次"谁 -> 去哪/已到"
	_sched_ui_timer -= delta
	if _sched_ui_timer <= 0.0:
		_sched_ui_timer = 0.5
		_refresh_schedule_ui()

func _refresh_schedule_ui() -> void:
	var panel := $UI/SchedulePanel as PanelContainer
	panel.visible = ApiClient.backend_online and not ApiClient.last_world.is_empty()
	if not panel.visible:
		return
	var npcs_data: Dictionary = ApiClient.last_world.get("npcs", {})
	var lines: Array = ["— 当前剧情/日程 —"]
	for npc in npcs:
		var info: Dictionary = npcs_data.get(npc.npc_name, {})
		if info.is_empty():
			continue
		var cur: String = str(info.get("grid", ""))
		var tgt: String = str(info.get("target_grid", ""))
		var where: String = Game.room_name(cur)
		var status: String
		if cur == tgt:
			status = "[已到] " + where
		else:
			status = "%s -> %s" % [where, Game.room_name(tgt)]
		lines.append("%s  %s" % [npc.npc_name, status])
	($UI/SchedulePanel/ScheduleLabel as Label).text = "\n".join(lines)

func _on_advance_done() -> void:
	print("[Main] 时间已快进, 日程翻段生效")

func _toggle_debug() -> void:
	var on: bool = not $DebugDraw.enabled
	$DebugDraw.set_enabled(on)
	$Cam.set_target(null)
	follow_idx = -1
	($UI/InfoLabel as Label).text = "区域调试: 开" if on else "镜头: 自由"
	print("[Main] 区域调试视图: ", "开" if on else "关")

func _clock_str() -> String:
	var t := int(demo_clock) % 86400
	return "%02d:%02d" % [t / 3600, (t % 3600) / 60]

## 本地演示日程(断线兜底): 随机挑一个 NPC, 50% 回家, 50% 去随机房间
## (M2.2: ApiClient 在线时 _process 不再调用本函数, 移动由后端 target_grid 驱动,
##  npc.go_to_room 接口 M2.1 已定型, 前端主体零改动)
func _random_schedule() -> void:
	if npcs.is_empty():
		return
	var npc = npcs[randi() % npcs.size()]
	var rid: String
	if randf() < 0.5:
		rid = npc.home_room
	else:
		var ids: Array = Game.all_room_ids()
		rid = ids[randi() % ids.size()]
	npc.go_to_room(rid)
	print("[日程] %s -> %s" % [npc.npc_name, Game.room_name(rid)])

func _on_npc_arrived(npc) -> void:
	# 打印真实所在房间(而非 goal_room): 到达事件含"闲逛到达",
	# goal_room 只代表最后一次指派, 语义会误导
	var rid := Game.room_id_at(npc.global_position)
	if rid == "":
		rid = npc.goal_room
	print("[到达] %s @ %s (%s)" % [npc.npc_name,
		Game.room_name(rid), Game.room_type(rid)])
	ApiClient.report_arrival(npc)  # M2.2: 在线时上报位置(后端只更新事实, 不写记忆)

## M2.2: 后端连接状态切换
func _on_online_changed(online: bool) -> void:
	if not online:
		_sched_timer = 1.0  # 回退本地日程, 1 秒后接手
		($UI/SchedulePanel as PanelContainer).visible = false
	print("[Main] 驱动源: " + ("后端日程" if online else "本地演示(后端离线)"))

## 空格循环跟随
func cycle_follow():
	if npcs.is_empty():
		return null
	follow_idx = (follow_idx + 1) % npcs.size()
	var npc = npcs[follow_idx]
	$Cam.set_target(npc)
	($UI/InfoLabel as Label).text = "镜头跟随: " + npc.npc_name
	return npc

