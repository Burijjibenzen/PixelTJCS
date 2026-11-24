extends Node
## 从 /world 读取目标楼层和房间，向 /world/report 上报角色实际走到的位置。
## 断线兜底: 连续 FAIL_LIMIT 次失败 -> backend_online=false,
##          main.gd 自动回退本地演示日程; 后端恢复后自动切回。

signal online_changed(online: bool)
signal advance_done            # 快进请求成功(下一轮轮询自然刷新目标)
signal speed_changed
signal actor_stage_changed(npc: NPC)
signal control_error(message: String)

const BACKEND := "http://127.0.0.1:8000"
const PLAYER_CONFIG_PATH := "user://pixeltjcs-player.cfg"
const POLL_INTERVAL := 1.0   # 轮询周期(秒)
const REQ_TIMEOUT := 1.5     # 单请求超时(秒)
const FAIL_LIMIT := 3        # 连续失败多少次判定离线
const BASE_SPEED := 10.0
const SPEED_OPTIONS := [10.0, 20.0, 40.0, 60.0]  # 游戏分钟/现实秒；对应原速的1/2/4/6倍。

var backend_online := false
var web_mode := OS.has_feature("web")
var player_session := ""
var _observer_token := ""
var _observer_until := 0
var _observer_poll := 0.0
var _observer_pending := false
var _observer_http: HTTPRequest
var backend_url := BACKEND  # 测试/自托管可覆盖；默认开发地址不变。
var backend_time := "第1天 08:00:00"
var display_minutes := 480.0
var simulation_speed := BASE_SPEED
var base_speed := BASE_SPEED
var simulation_paused := false
var _clock_initialized := false
var _clock_correction := 0.0
var last_world := {}         # 最近一次 /world 快照(日程面板数据源)
var world_id := ""
var _previous_world_id := ""
var _cast_epoch := ""
var _initial_world := {}

var _npcs: Array = []        # main.gd 装配后注入的 NPC 引用
var _poll_timer := 0.5       # 开局稍等场景就绪
var _fail_count := 0
var _last_stream_world := -100.0  # M3: WebSocket 世界快照健康时不再发重复 HTTP 查询。
var _polling := false        # 轮询单飞行保护(get_http_client_status 有状态间隙, 不可靠)
var _http: HTTPRequest       # 轮询专用
var _http_post: HTTPRequest  # 上报专用(单飞行 + 队列, 防并发)
var _http_adv: HTTPRequest   # 快进专用
var _pending_positions := {}  # 每名 NPC 只保留最新待发送位置
var _sending_position := {}
var _confirmed_positions := {}
var _speed_pending := false
var _point_targets := {}
var _position_timer := 0.0
var position_report_interval := 0.25


func _ready() -> void:
	if web_mode:
		var browser_window := JavaScriptBridge.get_interface("window")
		backend_url = str(browser_window.location.origin) + "/api"
	else:
		var configured_url := OS.get_environment("PIXELTJCS_BACKEND_URL").strip_edges()
		if configured_url.is_empty():
			configured_url = OS.get_environment("ROAMCHESTRA_BACKEND_URL").strip_edges()
		if not configured_url.is_empty():
			backend_url = configured_url.trim_suffix("/")
		_migrate_player_config()
	_http = HTTPRequest.new()
	_http.timeout = REQ_TIMEOUT
	add_child(_http)
	_http.request_completed.connect(_on_poll)
	_http_post = HTTPRequest.new()
	_http_post.timeout = REQ_TIMEOUT
	add_child(_http_post)
	_http_post.request_completed.connect(_on_report_done)
	_http_adv = HTTPRequest.new()
	_http_adv.timeout = 5.0
	add_child(_http_adv)
	_http_adv.request_completed.connect(_on_advance_done)
	_observer_http = HTTPRequest.new()
	_observer_http.timeout = REQ_TIMEOUT
	add_child(_observer_http)
	_observer_http.request_completed.connect(_observer_driver_done)

func _migrate_player_config() -> void:
	if FileAccess.file_exists(PLAYER_CONFIG_PATH):
		return
	var old_path := OS.get_user_data_dir().get_base_dir().path_join("Roamchestra/roamchestra-player.cfg")
	if FileAccess.file_exists(old_path):
		var result := DirAccess.copy_absolute(old_path, ProjectSettings.globalize_path(PLAYER_CONFIG_PATH))
		if result != OK:
			push_warning("无法迁移原有玩家身份设置，错误码 %d" % result)

func can_control_world() -> bool:
	return not web_mode or not player_session.is_empty()

func can_drive_npcs() -> bool:
	return can_control_world() or Time.get_ticks_msec() < _observer_until

func set_player_session(session: String) -> void:
	if player_session == session:return
	player_session = session
	if web_mode:
		_observer_until = 0
		_observer_poll = 0.0
		_clear_position_reports()

func _clear_position_reports() -> void:
	_http_post.cancel_request()
	_sending_position.clear()
	_pending_positions.clear()
	_confirmed_positions.clear()
	_point_targets.clear()

func _observer_driver_tick(delta: float) -> void:
	if not web_mode or not backend_online or not player_session.is_empty():
		return
	if last_world.get("social", {}).get("player_occupied", false):
		return
	_observer_poll -= delta
	if _observer_poll > 0.0 or _observer_pending:
		return
	_observer_poll = 2.5
	if _observer_token.is_empty():
		_observer_token = Crypto.new().generate_random_bytes(16).hex_encode()
	var roster: Array[String] = []
	for npc in _npcs:
		if is_instance_valid(npc):
			roster.append(npc.npc_name)
	_observer_pending = _observer_http.request(backend_url + "/world/observer-driver", world_headers(),
		HTTPClient.METHOD_POST, JSON.stringify({"token": _observer_token, "roster": roster})) == OK

func _observer_driver_done(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	_observer_pending = false
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		return
	var reply = JSON.parse_string(body.get_string_from_utf8())
	if reply is Dictionary and reply.get("assigned", false) and not can_control_world() \
			and not last_world.get("social", {}).get("player_occupied", false):
		_observer_until = Time.get_ticks_msec() + 6000
	elif _observer_until != 0:
		_observer_until = 0
		_clear_position_reports()

func world_headers() -> PackedStringArray:
	var headers := PackedStringArray(["Content-Type: application/json"])
	if web_mode and not player_session.is_empty():
		headers.append("X-Player-Session: " + player_session)
	return headers

## main.gd 生成 NPC 后注入引用
func setup(npcs: Array) -> void:
	_npcs = npcs
	if not _initial_world.is_empty():
		_place_initial_npcs(_initial_world)
		_initial_world.clear()

func _place_initial_npcs(data: Dictionary) -> void:
	for npc in _npcs:
		if is_instance_valid(npc):
			var info: Dictionary = data.npcs.get(npc.npc_name, {})
			if Game.has_room(info.get("map_id", ""), info.get("room_id", "")):
				npc.reset_to(info.map_id, info.room_id)


func _process(delta: float) -> void:
	_advance_display_clock(delta)
	_observer_driver_tick(delta)
	_position_timer -= delta
	if backend_online and _position_timer <= 0.0:
		_position_timer = position_report_interval
		for npc in _npcs:
			if is_instance_valid(npc):
				report_arrival(npc, true)
	_poll_timer -= delta
	if _poll_timer <= 0.0:
		_poll_timer = POLL_INTERVAL
		_poll()


func _poll() -> void:
	if Time.get_ticks_msec() / 1000.0 - _last_stream_world < 5.0:
		return
	if _polling:
		return  # 上一发还没回来(超时保护)
	_polling = true
	if _http.request(backend_url + "/world", [], HTTPClient.METHOD_GET) != OK:
		_polling = false


func _on_poll(result: int, code: int, _headers: PackedStringArray,
		body: PackedByteArray) -> void:
	_polling = false
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		if Time.get_ticks_msec() / 1000.0 - _last_stream_world < 5.0:
			return
		_fail_count += 1
		if _fail_count >= FAIL_LIMIT and backend_online:
			# M3: 断线释放后端锁，本地气泡自行结束。
			for npc in _npcs:
				npc.social_busy = false
				npc.hold_position(false)
				npc.encounter_wait = ""
			backend_online = false
			print("[ApiClient] 后端不可达(连续 %d 次), 回退本地演示日程" % FAIL_LIMIT)
			online_changed.emit(false)
		return
	var data = JSON.parse_string(body.get_string_from_utf8())
	if not data is Dictionary or not data.get("npcs") is Dictionary:
		return
	_apply_world(data)

# M3: HTTP 查询与 WS 推送复用一次状态应用，不能让旧 HTTP 超时覆盖新快照。
func apply_stream_world(data: Dictionary) -> void:
	if not data.get("npcs") is Dictionary:
		return
	_last_stream_world = Time.get_ticks_msec() / 1000.0
	_apply_world(data)

func _apply_world(data: Dictionary) -> void:
	_fail_count = 0
	var social: Dictionary = data.get("social", {})
	if social.get("player_occupied", false) and _observer_until != 0:
		_observer_until = 0
		_clear_position_reports()
	var next_world: String = social.get("world_id", "")
	if not next_world.is_empty():
		if next_world == _previous_world_id:
			return
		if not world_id.is_empty() and next_world != world_id:
			_previous_world_id = world_id
			_http.cancel_request()
			_http_post.cancel_request()
			_http_adv.cancel_request()
			_polling = false
			_speed_pending = false
			_sending_position.clear()
			_confirmed_positions.clear()
			_pending_positions.clear()
			_point_targets.clear()
			_clock_initialized = false
			_clock_correction = 0.0
			_place_initial_npcs(data)
			if FileAccess.file_exists(PLAYER_CONFIG_PATH):
				ConfigFile.new().save(PLAYER_CONFIG_PATH)
			if get_tree().current_scene != null:
				_initial_world = data.duplicate(true)
				get_tree().paused = false
				get_tree().reload_current_scene.call_deferred()
		world_id = next_world
	var next_epoch: String = social.get("epoch", _cast_epoch)
	var new_epoch := next_epoch != _cast_epoch
	_cast_epoch = next_epoch
	var previous_world := last_world
	position_report_interval = clampf(float(social.get("position_report_seconds", 0.25)), 0.1, 1.0)
	base_speed = float(social.get("base_speed", BASE_SPEED))
	simulation_speed = float(social.get("speed", BASE_SPEED))
	simulation_paused = bool(social.get("paused", false))
	if data.has("minutes"):
		if not _clock_initialized:
			display_minutes = float(data.minutes)
			_clock_initialized = true
		else:
			_clock_correction = float(data.minutes) - display_minutes
	last_world = data
	_refresh_clock_text()
	speed_changed.emit()
	if not backend_online:
		backend_online = true
		_confirmed_positions.clear()
		_pending_positions.clear()
		_point_targets.clear()
		print("[ApiClient] 已连接后端, 切换为后端日程驱动")
		online_changed.emit(true)
	for npc in _npcs:
		if not is_instance_valid(npc):
			continue
		var info: Dictionary = data["npcs"].get(npc.npc_name, {})
		if info.is_empty():
			continue
		var version := int(info.get("cast_version", npc.cast_version))
		if not new_epoch and version < npc.cast_version:
			data.npcs[npc.npc_name] = previous_world.get("npcs", {}).get(npc.npc_name, info)
			continue
		npc.cast_version = version
		var on_stage := bool(info.get("on_stage", npc.on_stage))
		if npc.on_stage != on_stage:
			if not on_stage and new_epoch:
				npc.reset_to(info.map_id, info.room_id)
			npc.on_stage = on_stage
			npc.visible = on_stage
			npc.social_busy = false
			_pending_positions.erase(npc.npc_name)
			_confirmed_positions.erase(npc.npc_name)
			actor_stage_changed.emit(npc)
		if not npc.on_stage:
			continue
		if not can_drive_npcs():
			npc.apply_spectator_position(info)
			npc.social_busy = bool(info.get("busy", false))
			continue
		npc.social_busy = bool(info.get("busy", false)) or bool(info.get("player_engaged", false))
		npc.hold_position(bool(info.get("hold_position", false)))
		npc.encounter_wait = str(info.get("encounter_wait", ""))
		if npc.position_held:
			_point_targets.erase(npc.npc_name)
		if npc.movement_locked():
			report_arrival(npc)
			continue
		var target_map := str(info.get("target_map_id", Game.FLOOR3))
		var target_room := str(info.get("target_room_id", info.get("target_grid", "")))
		var point: Dictionary = info.get("target_position", {})
		if point.has("x") and point.has("y") and target_map == npc.map_id:
			var destination: Vector2 = Game.map_offset(target_map) + Vector2(float(point.x), float(point.y))
			if Game.room_id_at(destination, target_map) == target_room and (_point_targets.get(npc.npc_name) != destination or npc.goal_room != target_room):
				if npc.go_to(destination):
					_point_targets[npc.npc_name] = destination
		elif target_room != "":
			var restore_room := _point_targets.has(npc.npc_name)
			_point_targets.erase(npc.npc_name)
			if restore_room or target_map != npc.goal_map_id or target_room != npc.goal_room:
				npc.go_to_location(target_map, target_room)
		var confirmed: Dictionary = _confirmed_positions.get(npc.npc_name, {})
		if confirmed.has("map_id") and (confirmed.map_id != info.get("map_id") or confirmed.room_id != info.get("room_id")):
			_confirmed_positions.erase(npc.npc_name)
		report_arrival(npc)


func time_scale() -> float:
	return 0.0 if simulation_paused else simulation_speed / base_speed


func _advance_display_clock(delta: float) -> void:
	var advance := delta * simulation_speed if not simulation_paused else 0.0
	# 后端快照之间连续计时，缓慢校准误差，不能把显示时钟向后跳。
	var correction := clampf(_clock_correction, -advance * 0.25, advance * 0.25) if backend_online else 0.0
	display_minutes += advance + correction
	_clock_correction -= correction
	_refresh_clock_text()


func _refresh_clock_text() -> void:
	var seconds := int(display_minutes * 60.0)
	backend_time = "第%d天 %02d:%02d:%02d" % [seconds / 86400 + 1, seconds / 3600 % 24, seconds / 60 % 60, seconds % 60]


func set_speed(multiplier: float) -> void:
	if not backend_online:
		simulation_speed = multiplier
		speed_changed.emit()
		return
	_send_world_control("/world/speed", {"speed": multiplier})

func set_paused(value: bool) -> void:
	if not backend_online:
		simulation_paused = value
		speed_changed.emit()
		return
	_send_world_control("/world/speed", {"paused": value})

func _send_world_control(path: String, payload: Dictionary) -> void:
	if _speed_pending:
		return
	_speed_pending = true
	var error := _http_adv.request(backend_url + path, world_headers(), HTTPClient.METHOD_POST,
		JSON.stringify(payload))
	if error != OK:
		_speed_pending = false
		control_error.emit("时间控制请求未能发送")


func report_arrival(npc, refresh := false) -> void:
	if not backend_online or not npc.on_stage or not can_drive_npcs():
		return
	var room := Game.room_id_at(npc.global_position, npc.map_id)
	var info: Dictionary = last_world.get("npcs", {}).get(npc.npc_name, {})
	if room.is_empty() or info.is_empty():
		return  # 门洞可能位于两个房间之间
	var item := {"npc": npc.npc_name, "map_id": npc.map_id, "room_id": room, "arrived": not npc.is_travelling()}
	var local_position: Vector2 = npc.global_position - Game.map_offset(npc.map_id)
	item["x"] = snappedf(local_position.x, 0.1)
	item["y"] = snappedf(local_position.y, 0.1)
	item["cast_version"] = npc.cast_version
	if not npc.encounter_wait.is_empty():
		item["encounter_wait"] = npc.encounter_wait
	if not world_id.is_empty():
		item["world_id"] = world_id
	if info.has("activity"):
		item["activity_id"] = str(info.activity.get("id", ""))
	if not info.has("target_map_id"):
		if npc.map_id != Game.FLOOR3:
			return
		var grid := room
		if Game.floor_data[Game.FLOOR3].waypoint_rooms.get(npc.goal_room, "") == room:
			grid = npc.goal_room
		item = {"npc": npc.npc_name, "grid": grid}
	if item == _sending_position or (not refresh and _sending_position.get("npc") != npc.npc_name and _confirmed_positions.get(npc.npc_name) == item):
		_pending_positions.erase(npc.npc_name)
		return
	_pending_positions[npc.npc_name] = item
	_pump_report()


func _pump_report() -> void:
	if not backend_online or not can_drive_npcs() or not _sending_position.is_empty() or _pending_positions.is_empty():
		return
	var who: String = _pending_positions.keys()[0]
	_sending_position = _pending_positions[who]
	_pending_positions.erase(who)
	var headers := world_headers()
	if not can_control_world():
		headers.append("X-Observer-Driver: " + _observer_token)
	var error := _http_post.request(backend_url + "/world/report",
		headers, HTTPClient.METHOD_POST, JSON.stringify(_sending_position))
	if error != OK:
		_sending_position = {}  # 下一次轮询会重新读取角色的实际位置


func _on_report_done(result: int, code: int, _headers: PackedStringArray,
		body: PackedByteArray) -> void:
	if code == 403 and not can_control_world():
		_observer_until = 0
		_clear_position_reports()
		return
	var response = null if body.is_empty() else JSON.parse_string(body.get_string_from_utf8())
	if result == HTTPRequest.RESULT_SUCCESS and code == 200 and not _sending_position.is_empty():
		# busy 表示服务端本次没有处理位置；解除占用后仍需重报，不能缓存为已确认。
		if not response is Dictionary or response.get("detail", "") != "busy":
			_confirmed_positions[_sending_position.npc] = _sending_position
	_sending_position = {}
	_pump_report()


# 指定时长的连续快进，保留给演示控制；界面日常操作使用 set_speed。
func advance_time(minutes: int) -> void:
	if not backend_online:
		return
	_send_world_control("/world/advance", {"minutes": minutes})


func _on_advance_done(result: int, code: int, _headers: PackedStringArray,
		body: PackedByteArray) -> void:
	_speed_pending = false
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		control_error.emit("倍速切换失败，请检查后端连接")
		return
	var data = JSON.parse_string(body.get_string_from_utf8())
	if data is Dictionary:
		simulation_speed = float(data.get("speed", simulation_speed))
		simulation_paused = bool(data.get("paused", simulation_paused))
		speed_changed.emit()
	advance_done.emit()
