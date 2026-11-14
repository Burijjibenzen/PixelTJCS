extends Node2D

const NPC_SCENE := preload("res://scenes/npc.tscn")
const ROSTER_PATH := "res://data/npc_roster.json"
const MapDoors := preload("res://scripts/map_doors.gd")

var npcs: Array[NPC] = []
var floor_doors := {}
var follow_idx := -1
var current_map := Game.FLOOR3
var _sched_timer := 2.0
var _dbg_prev := false
var _sched_ui_timer := 0.0

@onready var camera := $Cam
@onready var floor_picker: OptionButton = $UI/FloorPicker

func _ready() -> void:
	for map_id: String in Game.MAP_IDS:
		var floor_node := $Maps.get_node(map_id) as Node2D
		floor_node.position = Game.map_offset(map_id)
		Game.bind_room_layers(floor_node, map_id)
		floor_doors[map_id] = MapDoors.new(floor_node.get_node("door"), Game.floor_data[map_id].doors)
		floor_picker.add_item(Game.map_name(map_id))
	floor_picker.item_selected.connect(_select_floor)
	var roster: Array = Game._load_json(ROSTER_PATH).get("npcs", [])
	for entry: Dictionary in roster:
		var npc := NPC_SCENE.instantiate() as NPC
		var home_map: String = entry.get("home_map", Game.FLOOR3)
		$Actors.add_child(npc)
		npc.global_position = Game.nav_world_of(entry.home, home_map)
		npc.setup(entry.id, entry.name, int(entry.char), entry.home,
			Color(entry.tint[0], entry.tint[1], entry.tint[2]), home_map)
		npc.arrived.connect(_on_npc_arrived)
		npc.floor_changed.connect(_on_floor_changed)
		npcs.append(npc)
		if entry.has("offline_destination"):
			var destination: Dictionary = entry.offline_destination
			npc.go_to_location(destination.map_id, destination.room_id)

	print("[Main] 已生成 %d 个 NPC" % npcs.size())
	$DebugDraw.setup(npcs)
	_select_floor(0)
	# M3: 新增独立社交 UI；地图和日程逻辑保持原样。
	var social = load("res://scripts/social_ui.gd").new()
	social.setup(npcs)
	add_child(social)
	ApiClient.setup(npcs)
	ApiClient.online_changed.connect(_on_online_changed)
	$UI/BtnAdv1h.pressed.connect(func() -> void: ApiClient.advance_time(60))
	$UI/BtnAdv4h.pressed.connect(func() -> void: ApiClient.advance_time(240))
	for rate in ApiClient.SPEED_OPTIONS:
		$UI/SpeedPicker.add_item("正常 ×1" if rate == ApiClient.BASE_SPEED else "快进 ×%.0f" % (rate / ApiClient.BASE_SPEED))
	$UI/SpeedPicker.item_selected.connect(func(index): ApiClient.set_speed(ApiClient.SPEED_OPTIONS[index]))
	ApiClient.speed_changed.connect(_sync_speed_picker)
	_sync_speed_picker()
	_on_online_changed(ApiClient.backend_online)

func _physics_process(_delta: float) -> void:
	for map_id: String in floor_doors:
		floor_doors[map_id].update_for_npcs(npcs, map_id)

func _process(delta: float) -> void:
	var mode := "后端驱动" if ApiClient.backend_online else "本地演示"
	var rate := "已暂停" if ApiClient.simulation_paused else "×%.0f" % ApiClient.time_scale()
	$UI/ClockLabel.text = ApiClient.backend_time + " · " + mode + " " + rate
	if not ApiClient.backend_online:
		_sched_timer -= delta * ApiClient.time_scale()
		if _sched_timer <= 0.0:
			_sched_timer = randf_range(4.0, 7.0)
			_random_schedule()

	var debug_key := Input.is_key_pressed(KEY_F3) or Input.is_key_pressed(KEY_V)
	if debug_key and not _dbg_prev:
		$DebugDraw.set_enabled(not $DebugDraw.enabled)
	_dbg_prev = debug_key
	_sched_ui_timer -= delta
	if _sched_ui_timer <= 0.0:
		_sched_ui_timer = 0.5
		_refresh_schedule_ui()

func _unhandled_key_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode >= KEY_1 and event.keycode <= KEY_3:
			_select_floor(event.keycode - KEY_1)
			get_viewport().set_input_as_handled()

func _select_floor(index: int) -> void:
	cancel_follow()
	var map_id: String = Game.MAP_IDS[index]
	# 切楼时先看这一层实有的角色；无人时以公共走廊为起点。
	for npc in npcs:
		if npc.map_id == map_id:
			_show_floor(map_id, npc.global_position)
			return
	var first_room: String = str(Game.all_room_ids(map_id)[0])
	_show_floor(map_id, Game.nav_world_of(first_room, map_id))

func _show_floor(map_id: String, focus: Vector2) -> void:
	current_map = map_id
	floor_picker.select(Game.MAP_IDS.find(map_id))
	$DebugDraw.map_id = map_id
	$DebugDraw.queue_redraw()
	var offset := Game.map_offset(map_id)
	var size: Vector2i = Game.floor_data[map_id].size
	camera.limit_left = int(offset.x)
	camera.limit_top = int(offset.y)
	camera.limit_right = int(offset.x) + size.x * Game.tile_size
	camera.limit_bottom = int(offset.y) + size.y * Game.tile_size
	camera.global_position = focus
	camera.reset_smoothing()

func cancel_follow() -> void:
	camera.set_target(null)
	$UI/InfoLabel.text = "镜头：自由"

func cycle_follow() -> void:
	if npcs.is_empty():
		return
	follow_idx = (follow_idx + 1) % npcs.size()
	var npc := npcs[follow_idx]
	_show_floor(npc.map_id, npc.global_position)
	camera.set_target(npc)
	$UI/InfoLabel.text = "镜头跟随：" + npc.npc_name

func _on_floor_changed(npc: NPC) -> void:
	if camera.target == npc:
		_show_floor(npc.map_id, npc.global_position)
	ApiClient.report_arrival(npc)

func _on_npc_arrived(npc: NPC) -> void:
	var room := Game.room_id_at(npc.global_position, npc.map_id)
	print("[到达] %s @ %s" % [npc.npc_name, _location_name(npc.map_id, room)])
	ApiClient.report_arrival(npc)

func _location_name(map_id: String, room_id: String) -> String:
	return Game.map_name(map_id) + "·" + Game.room_name(room_id, map_id)

func _refresh_schedule_ui() -> void:
	var panel := $UI/SchedulePanel as PanelContainer
	panel.visible = ApiClient.backend_online and not ApiClient.last_world.is_empty()
	if not panel.visible:
		return
	var npcs_data: Dictionary = ApiClient.last_world.get("npcs", {})
	var lines: PackedStringArray = ["今日活动 · 实际行走后开始停留\n"]
	for npc in npcs:
		var info: Dictionary = npcs_data.get(npc.npc_name, {})
		if info.is_empty():
			continue
		var map_id: String = info.get("map_id", Game.FLOOR3)
		var room: String = info.get("room_id", info.get("grid", ""))
		var target_map: String = info.get("target_map_id", Game.FLOOR3)
		var target_room: String = info.get("target_room_id", info.get("target_grid", ""))
		var place := _location_name(map_id, room)
		var activity: Dictionary = info.get("activity", {})
		if not activity.is_empty():
			lines.append("%s　[%s] %s" % [npc.npc_name, activity.get("state_label", "等待"), activity.get("title", "基础作息")])
			lines.append("目的地：%s" % _location_name(target_map, target_room))
			lines.append("原因：%s" % activity.get("reason", "沿用基础作息"))
			lines.append("%s\n" % activity.get("detail", ""))
		elif map_id == target_map and room == target_room and not npc.is_travelling():
			lines.append("%s　[已到] %s" % [npc.npc_name, place])
		else:
			lines.append("%s　%s → %s" % [npc.npc_name, place, _location_name(target_map, target_room)])
	$UI/SchedulePanel/Scroll/ScheduleLabel.text = "\n".join(lines)

# 随机演示只给空闲角色分配新目标，跨层行程会完整走完。
func _random_schedule() -> void:
	var idle := npcs.filter(func(npc: NPC) -> bool: return not npc.is_travelling())
	if idle.is_empty():
		return
	var npc: NPC = idle.pick_random()
	var map_id := npc.home_map_id
	var room := npc.home_room
	if randf() < 0.5:
		map_id = Game.MAP_IDS.pick_random()
		room = Game.all_room_ids(map_id).pick_random()
	if npc.go_to_location(map_id, room):
		print("[日程] %s → %s" % [npc.npc_name, _location_name(map_id, room)])

func _on_online_changed(online: bool) -> void:
	$UI/BtnAdv1h.disabled = not online
	$UI/BtnAdv4h.disabled = not online
	if not online:
		$UI/SchedulePanel.visible = false
		_sched_timer = 2.0

func _sync_speed_picker() -> void:
	var index := ApiClient.SPEED_OPTIONS.find(ApiClient.simulation_speed)
	if index >= 0:
		$UI/SpeedPicker.select(index)
