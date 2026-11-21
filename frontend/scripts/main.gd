extends Node2D

const NPC_SCENE := preload("res://scenes/npc.tscn")
const ROSTER_PATH := "res://data/npc_roster.json"
const MapDoors := preload("res://scripts/map_doors.gd")
const CHAT_RADIUS := 96.0
const BOARD_RADIUS := 384.0
const MAP_DRAG_THRESHOLD := 5.0

var _map_press := false
var _map_dragging := false
var _map_drag_origin := Vector2.ZERO
var _map_pointer := Vector2.ZERO

var npcs: Array[NPC] = []
var floor_doors := {}
var follow_idx := -1
var current_map := Game.FLOOR3
var _sched_timer := 2.0
var _dbg_prev := false
var social_ui
var notice_ui
var player_ui
var player_avatar: PlayerAvatar
var interaction_marks := preload("res://scripts/interaction_marks.gd").new()
var character_status := preload("res://scripts/character_status.gd").new()
var _view_before_preview := {}

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
	social_ui = load("res://scripts/social_ui.gd").new()
	social_ui.setup(npcs)
	add_child(social_ui)
	notice_ui = load("res://scripts/notice_board.gd").new()
	notice_ui.setup(npcs)
	add_child(notice_ui)
	$MapBrowser.notice_board = notice_ui
	$MapBrowser.notice_board_requested.connect(_open_notice_board)
	player_ui = load("res://scripts/player_chat.gd").new()
	player_ui.setup(npcs)
	add_child(player_ui)
	player_avatar = load("res://scripts/player_avatar.gd").new()
	$Actors.add_child(player_avatar)
	player_avatar.global_position = Game.nav_world_of("R01", Game.FLOOR3)
	player_avatar.visible = false
	player_ui.player_avatar = player_avatar
	player_ui.near_checker = Callable(self, "_near_npc")
	interaction_marks.populate = _populate_interaction_marks
	interaction_marks.z_index = -1
	$UI.add_child(interaction_marks)
	character_status.name = "CharacterStatus"
	character_status.setup(npcs, player_avatar, player_ui, notice_ui)
	character_status.preview_changed.connect(_preview_character)
	character_status.character_selected.connect(_select_character)
	character_status.stage_requested.connect(notice_ui.set_actor_stage)
	notice_ui.stage_selection_changed.connect(character_status.set_stage_selection)
	$UI.add_child(character_status)
	ApiClient.actor_stage_changed.connect(_on_actor_stage_changed)
	$MapBrowser.visibility_changed.connect(_end_status_preview)
	var mechanics = load("res://scripts/mechanics_panel.gd").new()
	mechanics.name = "MechanicsPanel"
	add_child(mechanics)
	ApiClient.setup(npcs)
	ApiClient.online_changed.connect(_on_online_changed)
	$UI/BtnAdv1h.pressed.connect(func() -> void: ApiClient.advance_time(60))
	$UI/BtnAdv4h.pressed.connect(func() -> void: ApiClient.advance_time(240))
	$UI/BtnAdv3d.pressed.connect(func() -> void: ApiClient.advance_time(4320))
	$UI/BtnPause.pressed.connect(func() -> void: ApiClient.set_paused(not ApiClient.simulation_paused))
	$UI/MapBrowserButton.process_mode = Node.PROCESS_MODE_ALWAYS
	$UI/GodMode.pressed.connect(func() -> void:
		if notice_ui.visible and notice_ui.god_mode:
			notice_ui.hide()
			return
		var boards: Array = Game.floor_data[current_map].get("noticeboards", [])
		notice_ui.open_board("" if boards.is_empty() else str(boards[0].id), true))
	_layout_controls(mechanics)
	ApiClient.control_error.connect(func(message): $UI/InfoLabel.text = message)
	for rate in ApiClient.SPEED_OPTIONS:
		$UI/SpeedPicker.add_item("正常 ×1" if rate == ApiClient.BASE_SPEED else "快进 ×%.0f" % (rate / ApiClient.BASE_SPEED))
	$UI/SpeedPicker.item_selected.connect(func(index): ApiClient.set_speed(ApiClient.SPEED_OPTIONS[index]))
	ApiClient.speed_changed.connect(_sync_speed_picker)
	_sync_speed_picker()
	_on_online_changed(ApiClient.backend_online)

func _layout_controls(mechanics) -> void:
	var bar := HBoxContainer.new()
	bar.name = "ModeBar"
	bar.position = Vector2(12, 42)
	bar.add_theme_constant_override("separation", 8)
	var tea: Theme = preload("res://assets/tea_theme.tres")
	bar.theme = tea
	$UI.add_child(bar)
	for button: Control in [$UI/MapBrowserButton, floor_picker, social_ui.open_button, $UI/GodMode, player_ui.mode_button]:
		button.reparent(bar, false)
		button.set_anchors_preset(Control.PRESET_TOP_LEFT)
		button.custom_minimum_size.y = 36
	for item: Control in [$UI/BtnAdv1h, $UI/BtnAdv4h, $UI/BtnAdv3d, $UI/SpeedPicker, $UI/BtnPause, $UI/ClockLabel, $UI/InfoLabel, $UI/HintLabel]:
		item.theme = tea
	var windows := [social_ui.panel, notice_ui, player_ui.window, mechanics.window, $MapBrowser]
	for window in windows:
		window.visibility_changed.connect(func():
			if not window.visible:
				return
			for other in windows:
				if other == window or not other.visible:
					continue
				if other == player_ui.window:
					player_ui._close_panel()
				elif other == $MapBrowser:
					# 地图内打开公告时，保留原来的地图视角。
					if not (window == notice_ui and not notice_ui.god_mode):
						$MapBrowser.close()
				else:
					other.hide()
		)

func _physics_process(_delta: float) -> void:
	for map_id: String in floor_doors:
		var player_cell := Game.cell_of(player_avatar.global_position, map_id) if player_avatar.visible and player_avatar.map_id == map_id else Vector2i(-1, -1)
		floor_doors[map_id].update_for_npcs(npcs, map_id, player_cell)
	if _can_control_player() and player_avatar.walk(_delta):
		var player_floor := player_avatar.map_id
		var cell := Game.cell_of(player_avatar.global_position, player_floor)
		var destination: Dictionary = Game.floor_data[player_floor].walk_stairs.get(cell, {})
		if not destination.is_empty():
			player_avatar.map_id = destination.map_id
			player_avatar.global_position = Game.world_of(destination.cell, destination.map_id)
			if _view_before_preview.is_empty() or camera.target == player_avatar:
				_show_floor(destination.map_id, player_avatar.global_position)
			if _view_before_preview.is_empty():
				$UI/InfoLabel.text = "你已到达" + Game.map_name(destination.map_id)
		if _view_before_preview.is_empty() and not _map_dragging:
			camera.set_target(player_avatar)

func _process(delta: float) -> void:
	if player_avatar.visible != player_ui.active:
		player_avatar.visible = player_ui.active
		if player_avatar.visible:
			_focus_player()
			$UI/HintLabel.text = "WASD/方向键行走　按住左键拖地图　E 交互　踩箭头换层　P 回到自己"
		else:
			cancel_follow()
			$UI/HintLabel.text = "点击人物：跟随/资料　1/2/3 切楼　按住左键拖地图　滚轮缩放　空格切换　Esc 取消　V 分区"
	var mode := "后端驱动" if ApiClient.backend_online else "本地演示"
	var rate := "×%.0f" % ApiClient.time_scale()
	var social: Dictionary = ApiClient.last_world.get("social", {})
	var stream := "对话事件流已连接" if social_ui.stream_online else "对话事件流重连中"
	var model := "模型已配置" if social.get("model_configured", false) else "模型未配置"
	if not ApiClient.backend_online:
		model = "模型状态未知"
	var time_control := "暂停" if ApiClient.simulation_paused else rate
	if not ApiClient.simulation_paused and social.get("target_minutes") != null:
		time_control += " 定时快进"
	$UI/ClockLabel.text = " · ".join([ApiClient.backend_time, mode + " " + time_control, stream, model])
	$UI/ClockLabel.tooltip_text = social_ui.notice if Time.get_ticks_msec() / 1000.0 < social_ui.notice_until else "模型配置仅表示已填写密钥"
	if not ApiClient.backend_online:
		_sched_timer -= delta * ApiClient.time_scale()
		if _sched_timer <= 0.0:
			_sched_timer = randf_range(4.0, 7.0)
			_random_schedule()

	var focus := get_viewport().gui_get_focus_owner()
	var typing := focus is LineEdit or focus is TextEdit
	var debug_key := not typing and (Input.is_key_pressed(KEY_F3) or Input.is_key_pressed(KEY_V))
	if debug_key and not _dbg_prev:
		$DebugDraw.set_enabled(not $DebugDraw.enabled)
	_dbg_prev = debug_key

func _unhandled_key_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		var focus := get_viewport().gui_get_focus_owner()
		if focus is LineEdit or focus is TextEdit:return
		if _can_control_player() and event.keycode == KEY_E:
			_interact_player()
			get_viewport().set_input_as_handled()
			return
		if player_avatar.visible and event.keycode == KEY_P:
			_focus_player()
			get_viewport().set_input_as_handled()
			return
		if event.keycode >= KEY_1 and event.keycode <= KEY_3:
			_select_floor(event.keycode - KEY_1)
			get_viewport().set_input_as_handled()

func _unhandled_input(event: InputEvent) -> void:
	# GUI先消费点击，面板和地图试走中的交互不会点中背后的NPC。
	if $MapBrowser.visible or not event is InputEventMouseButton or not event.pressed or event.button_index != MOUSE_BUTTON_LEFT:
		return
	_map_press = true
	_map_dragging = false
	_map_drag_origin = event.position
	_map_pointer = event.position
	get_viewport().set_input_as_handled()

func _input(event: InputEvent) -> void:
	if not _map_press:
		return
	if event is InputEventMouseMotion:
		if not event.button_mask & MOUSE_BUTTON_MASK_LEFT:
			_map_press = false
			_map_dragging = false
			return
		if not _map_dragging and event.position.distance_to(_map_drag_origin) >= MAP_DRAG_THRESHOLD:
			cancel_follow()
			_map_dragging = true
		if _map_dragging:
			camera.pan(event.position - _map_pointer)
			_map_pointer = event.position
			get_viewport().set_input_as_handled()
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and not event.pressed:
		var clicked: bool = not _map_dragging and event.position.distance_to(_map_drag_origin) < MAP_DRAG_THRESHOLD
		_map_press = false
		_map_dragging = false
		if clicked:
			_click_map(_map_drag_origin)
		get_viewport().set_input_as_handled()

func _click_map(point: Vector2) -> void:
	var target := _target_at(point)
	if target.is_empty():
		return
	if target.has("npc"):
		var npc: NPC = target.npc
		follow_npc(npc)
		if player_ui.active and _near_npc(npc.npc_name):
			if player_ui.window.visible and player_ui.selected_npc == npc.npc_name:
				player_ui._close_panel()
			else:
				player_ui.open_for(npc.npc_name)
		else:
			social_ui.show_profile(npc.npc_name, true)
	else:
		_open_notice_board(target.board.id)

func _target_at(viewport_position: Vector2) -> Dictionary:
	var picked: NPC = null
	for npc in npcs:
		if npc.map_id != current_map or not npc.is_visible_in_tree() or not npc.hit_test(viewport_position):
			continue
		# 与Actors的Y排序一致；重叠时优先选最前面的人物。
		if picked == null or npc.global_position.y >= picked.global_position.y:
			picked = npc
	if picked != null:
		return {"npc": picked}
	var world_point := get_canvas_transform().affine_inverse() * viewport_position
	var cell := Game.cell_of(world_point, current_map)
	for board: Dictionary in Game.floor_data[current_map].get("noticeboards", []):
		for raw: Array in board.cells:
			if cell == Vector2i(int(raw[0]), int(raw[1])):
				return {"board": board}
	return {}

func _open_notice_board(board_id: String) -> void:
	if board_id.is_empty():
		var floor_boards: Array = Game.floor_data[current_map].get("noticeboards", [])
		if not floor_boards.is_empty():
			board_id = str(floor_boards[0].id)
	if notice_ui.visible and not notice_ui.god_mode and notice_ui.board_id == board_id:
		notice_ui.hide()
	else:
		notice_ui.open_board(board_id)

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
	_end_status_preview()
	camera.set_target(null)
	$UI/InfoLabel.text = "镜头：自由"

func cycle_follow() -> void:
	for offset in range(1, npcs.size() + 1):
		var npc := npcs[(follow_idx + offset) % npcs.size()]
		if npc.on_stage:
			follow_npc(npc)
			return

func _on_actor_stage_changed(npc: NPC) -> void:
	if not npc.on_stage:
		social_ui.stop_actor_playback(npc.npc_name)
		if camera.target == npc:
			cancel_follow()
		if player_ui.conversation_npc == npc.npc_name or player_ui.engage_target == npc.npc_name:
			player_ui._close_panel()
	character_status._refresh_rows()

func follow_npc(npc: NPC) -> void:
	_view_before_preview.clear()
	follow_idx = npcs.find(npc)
	_show_floor(npc.map_id, npc.global_position)
	camera.set_target(npc)
	$UI/InfoLabel.text = "镜头跟随：" + npc.npc_name

func _focus_player() -> void:
	_view_before_preview.clear()
	_show_floor(player_avatar.map_id, player_avatar.global_position)
	camera.set_target(player_avatar)
	$UI/InfoLabel.text = "镜头跟随：你"

func _select_character(actor: Node2D) -> void:
	if actor == player_avatar:
		_focus_player()
		player_ui.toggle_panel()
	else:
		var npc := actor as NPC
		if npc.on_stage:
			follow_npc(npc)
		social_ui.show_profile(npc.npc_name, true)

func _preview_character(actor: Node2D) -> void:
	if actor == null:
		_end_status_preview()
		return
	if _view_before_preview.is_empty():
		_view_before_preview = {
			"map_id": current_map, "position": camera.global_position, "zoom": camera.zoom,
			"target": camera.target, "label": $UI/InfoLabel.text,
			"viewing_player_floor": current_map == player_avatar.map_id,
		}
	_show_floor(actor.map_id, actor.global_position)
	camera.set_target(actor)
	var actor_name: String = player_ui.profile_name if actor == player_avatar else actor.npc_name
	$UI/InfoLabel.text = "镜头预览：" + actor_name

func _end_status_preview() -> void:
	if _view_before_preview.is_empty():
		return
	var view := _view_before_preview
	_view_before_preview = {}
	var target: Node2D = view.target
	if not is_instance_valid(target) or not target.is_visible_in_tree():
		target = null
	var map_id: String = view.map_id
	var position: Vector2 = view.position
	if target != null and target.map_id != map_id:
		map_id = target.map_id
		position = target.global_position
	camera.zoom = view.zoom
	_show_floor(map_id, position)
	camera.set_target(target)
	$UI/InfoLabel.text = view.label

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

func _near_npc(npc_name: String) -> bool:
	if not player_avatar.visible:return false
	var player_room := Game.room_id_at(player_avatar.global_position, player_avatar.map_id)
	if player_room.is_empty():return false
	for npc in npcs:
		if npc.on_stage and npc.npc_name == npc_name and npc.map_id == player_avatar.map_id:
			return player_room == Game.room_id_at(npc.global_position, npc.map_id) \
				and player_avatar.global_position.distance_to(npc.global_position) <= CHAT_RADIUS
	return false

func _can_control_player() -> bool:
	var focus := get_viewport().gui_get_focus_owner()
	var viewing_player_floor: bool = _view_before_preview.get("viewing_player_floor", current_map == player_avatar.map_id)
	return player_avatar.visible and viewing_player_floor \
		and not $MapBrowser.visible and not player_ui.window.visible and not notice_ui.visible \
		and not (focus is LineEdit or focus is TextEdit)

func _nearby_target() -> Dictionary:
	var target := {}
	var distance := INF
	var pos := player_avatar.global_position
	var map_id := player_avatar.map_id
	var room := Game.room_id_at(pos, map_id)
	if ApiClient.backend_online and not ApiClient.simulation_paused:
		for npc in npcs:
			if npc.social_busy and player_ui.conversation_npc != npc.npc_name:
				continue
			if _near_npc(npc.npc_name):
				var gap := pos.distance_to(npc.global_position)
				if gap < distance:
					distance = gap
					target = {"npc": npc}
	for board: Dictionary in Game.floor_data[map_id].noticeboards:
		if board.room_id != room:
			continue
		for raw: Array in board.cells:
			var gap := pos.distance_to(Game.world_of(Vector2i(int(raw[0]), int(raw[1])), map_id))
			if gap <= BOARD_RADIUS and gap < distance:
				distance = gap
				target = {"board": board}
	return target

func _interact_player() -> void:
	var target := _nearby_target()
	if target.has("npc"):
		player_ui.open_for(target.npc.npc_name)
	elif target.has("board"):
		_open_notice_board(target.board.id)

func _populate_interaction_marks() -> void:
	if $MapBrowser.visible:
		return
	var to_marks := interaction_marks.get_global_transform_with_canvas().affine_inverse()
	var world_to_marks := to_marks * get_canvas_transform()
	interaction_marks.clip = Rect2(to_marks * get_viewport_rect().position, get_viewport_rect().size * to_marks.get_scale())
	for board: Dictionary in Game.floor_data[current_map].noticeboards:
		var point := Game.world_of(Vector2i(int(board.cell[0]), int(board.cell[1])), current_map)
		interaction_marks.labels.append({"text": "告示栏", "point": world_to_marks * (point - Vector2(0, 28))})
	var stairs: Dictionary = Game.floor_data[current_map].walk_stairs
	for cell: Vector2i in stairs:
		interaction_marks.arrows.append({"text": stairs[cell].label, "point": world_to_marks * Game.world_of(cell, current_map)})
	var nearby := _nearby_target() if _can_control_player() and current_map == player_avatar.map_id else {}
	if not nearby.is_empty():
		interaction_marks.show_prompt(world_to_marks * (player_avatar.global_position - Vector2(0, 42)))
		_outline_target(nearby, world_to_marks)
	var mouse: Vector2 = interaction_marks.pointer
	if get_viewport_rect().has_point(mouse) and get_viewport().gui_get_hovered_control() == null:
		var hovered := _target_at(mouse)
		if hovered != nearby:
			_outline_target(hovered, world_to_marks)

func _outline_target(target: Dictionary, world_to_marks: Transform2D) -> void:
	if target.has("npc"):
		interaction_marks.sprites.append(target.npc.sprite)
	elif target.has("board"):
		interaction_marks.outline_cells(target.board.cells, Game.world_corner(Vector2i.ZERO, current_map), world_to_marks)

# 随机演示只给空闲角色分配新目标，跨层行程会完整走完。
func _random_schedule() -> void:
	var idle := npcs.filter(func(npc: NPC) -> bool: return npc.on_stage and not npc.is_travelling())
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
		_sched_timer = 2.0

func _sync_speed_picker() -> void:
	$UI/BtnPause.text = "继续" if ApiClient.simulation_paused else "暂停"
	var index := ApiClient.SPEED_OPTIONS.find(ApiClient.simulation_speed)
	if index >= 0:
		$UI/SpeedPicker.select(index)
