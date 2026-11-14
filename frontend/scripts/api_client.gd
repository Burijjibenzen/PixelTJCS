extends Node
## 从 /world 读取目标楼层和房间，向 /world/report 上报角色实际走到的位置。
## 断线兜底: 连续 FAIL_LIMIT 次失败 -> backend_online=false,
##          main.gd 自动回退本地演示日程; 后端恢复后自动切回。

signal online_changed(online: bool)
signal advance_done            # 快进请求成功(下一轮轮询自然刷新目标)
signal speed_changed
signal control_error(message: String)

const BACKEND := "http://127.0.0.1:8000"
const POLL_INTERVAL := 1.0   # 轮询周期(秒)
const REQ_TIMEOUT := 1.5     # 单请求超时(秒)
const FAIL_LIMIT := 3        # 连续失败多少次判定离线
const BASE_SPEED := 10.0
const SPEED_OPTIONS := [10.0, 20.0, 40.0, 60.0]  # 游戏分钟/现实秒；对应原速的1/2/4/6倍。

var backend_online := false
var backend_url := BACKEND  # 测试/自托管可覆盖；默认开发地址不变。
var backend_time := "第1天 08:00:00"
var display_minutes := 480.0
var simulation_speed := BASE_SPEED
var base_speed := BASE_SPEED
var simulation_paused := false
var _clock_initialized := false
var _clock_correction := 0.0
var last_world := {}         # 最近一次 /world 快照(日程面板数据源)

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


func _ready() -> void:
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


## main.gd 生成 NPC 后注入引用
func setup(npcs: Array) -> void:
	_npcs = npcs


func _process(delta: float) -> void:
	_advance_display_clock(delta)
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
		print("[ApiClient] 已连接后端, 切换为后端日程驱动")
		online_changed.emit(true)
	for npc in _npcs:
		if not is_instance_valid(npc):
			continue
		var info: Dictionary = data["npcs"].get(npc.npc_name, {})
		if info.is_empty():
			continue
		# M3: 只同步忙碌状态，移动仍使用原有寻路接口。
		npc.social_busy = bool(info.get("busy", false))
		var target_map := str(info.get("target_map_id", Game.FLOOR3))
		var target_room := str(info.get("target_room_id", info.get("target_grid", "")))
		if target_room != "" and (target_map != npc.goal_map_id or target_room != npc.goal_room):
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
		simulation_paused = false
		speed_changed.emit()
		return
	if _speed_pending:
		return
	_speed_pending = true
	var error := _http_adv.request(backend_url + "/world/speed", ["Content-Type: application/json"], HTTPClient.METHOD_POST,
		JSON.stringify({"speed": multiplier, "paused": false}))
	if error != OK:
		_speed_pending = false
		control_error.emit("倍速切换失败，请稍后重试")


func report_arrival(npc) -> void:
	if not backend_online:
		return
	var room := Game.room_id_at(npc.global_position, npc.map_id)
	var info: Dictionary = last_world.get("npcs", {}).get(npc.npc_name, {})
	if room.is_empty() or info.is_empty():
		return  # 门洞可能位于两个房间之间
	var item := {"npc": npc.npc_name, "map_id": npc.map_id, "room_id": room, "arrived": not npc.is_travelling()}
	if info.has("activity"):
		item["activity_id"] = str(info.activity.get("id", ""))
	if not info.has("target_map_id"):
		if npc.map_id != Game.FLOOR3:
			return
		var grid := room
		if Game.floor_data[Game.FLOOR3].waypoint_rooms.get(npc.goal_room, "") == room:
			grid = npc.goal_room
		item = {"npc": npc.npc_name, "grid": grid}
	if item == _sending_position or (_sending_position.get("npc") != npc.npc_name and _confirmed_positions.get(npc.npc_name) == item):
		_pending_positions.erase(npc.npc_name)
		return
	_pending_positions[npc.npc_name] = item
	_pump_report()


func _pump_report() -> void:
	if not backend_online or not _sending_position.is_empty() or _pending_positions.is_empty():
		return
	var who: String = _pending_positions.keys()[0]
	_sending_position = _pending_positions[who]
	_pending_positions.erase(who)
	var error := _http_post.request(backend_url + "/world/report",
		["Content-Type: application/json"], HTTPClient.METHOD_POST, JSON.stringify(_sending_position))
	if error != OK:
		_sending_position = {}  # 下一次轮询会重新读取角色的实际位置


func _on_report_done(result: int, code: int, _headers: PackedStringArray,
		body: PackedByteArray) -> void:
	var response = JSON.parse_string(body.get_string_from_utf8())
	if result == HTTPRequest.RESULT_SUCCESS and code == 200 and not _sending_position.is_empty():
		# busy 表示服务端本次没有处理位置；解除占用后仍需重报，不能缓存为已确认。
		if not response is Dictionary or response.get("detail", "") != "busy":
			_confirmed_positions[_sending_position.npc] = _sending_position
	_sending_position = {}
	_pump_report()


# 指定时长的连续快进，保留给演示控制；界面日常操作使用 set_speed。
func advance_time(minutes: int) -> void:
	if not backend_online or _speed_pending:
		return
	_speed_pending = true
	var body := JSON.stringify({"minutes": minutes})
	print("[ApiClient] 快进 %d 游戏分钟..." % minutes)
	if _http_adv.request(backend_url + "/world/advance",
		["Content-Type: application/json"], HTTPClient.METHOD_POST, body) != OK:
		_speed_pending = false
		control_error.emit("快进请求未能发送")


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
