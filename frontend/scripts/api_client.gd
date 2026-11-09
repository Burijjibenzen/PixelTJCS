extends Node
## M2.2 后端联动客户端 (autoload: ApiClient)
## 职责: 轮询 GET /world -> 把每个 NPC 的 target_grid 下发给 npc.go_to_room();
##       NPC 到达后 POST /world/report 上报位置(后端只更新事实, 不写记忆)。
## 断线兜底: 连续 FAIL_LIMIT 次失败 -> backend_online=false,
##          main.gd 自动回退本地演示日程; 后端恢复后自动切回。

signal online_changed(online: bool)
signal advance_done            # 快进请求成功(下一轮轮询自然刷新目标)

const BACKEND := "http://127.0.0.1:8000"
const POLL_INTERVAL := 1.0   # 轮询周期(秒)
const REQ_TIMEOUT := 1.5     # 单请求超时(秒)
const FAIL_LIMIT := 3        # 连续失败多少次判定离线

var backend_online := false
var backend_time := ""       # 后端游戏时间字符串("第1天 08:00"), 在线时给 main 显示
var last_world := {}         # 最近一次 /world 快照(日程面板数据源)

var _npcs: Array = []        # main.gd 装配后注入的 NPC 引用
var _poll_timer := 0.5       # 开局稍等场景就绪
var _fail_count := 0
var _polling := false        # 轮询单飞行保护(get_http_client_status 有状态间隙, 不可靠)
var _http: HTTPRequest       # 轮询专用
var _http_post: HTTPRequest  # 上报专用(单飞行 + 队列, 防并发)
var _http_adv: HTTPRequest   # 快进专用
var _report_queue: Array = []  # [{npc, grid}]
var _reporting := false


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
	_poll_timer -= delta
	if _poll_timer <= 0.0:
		_poll_timer = POLL_INTERVAL
		_poll()


# ---- 轮询: GET /world -> 比对 target_grid, 变化才下发寻路 ----
func _poll() -> void:
	if _polling:
		return  # 上一发还没回来(超时保护)
	_polling = true
	_http.request(BACKEND + "/world", [], HTTPClient.METHOD_GET)


func _on_poll(result: int, code: int, _headers: PackedStringArray,
		body: PackedByteArray) -> void:
	_polling = false
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		_fail_count += 1
		if _fail_count >= FAIL_LIMIT and backend_online:
			backend_online = false
			print("[ApiClient] 后端不可达(连续 %d 次), 回退本地演示日程" % FAIL_LIMIT)
			online_changed.emit(false)
		return
	if not backend_online:
		backend_online = true
		print("[ApiClient] 已连接后端, 切换为后端日程驱动")
		online_changed.emit(true)
	_fail_count = 0
	var data = JSON.parse_string(body.get_string_from_utf8())
	if data == null or not data.has("npcs"):
		return
	backend_time = str(data.get("time", ""))
	last_world = data
	for npc in _npcs:
		if not is_instance_valid(npc):
			continue
		var info: Dictionary = data["npcs"].get(npc.npc_name, {})
		if info.is_empty():
			continue
		var tg := str(info.get("target_grid", ""))
		if tg != "" and tg != npc.goal_room:
			npc.go_to_room(tg)
			print("[指派] %s -> %s" % [npc.npc_name, Game.room_name(tg)])


# ---- 上报: 到达入队, 单飞行 POST /world/report ----
func report_arrival(npc) -> void:
	if not backend_online:
		return  # 离线模式(本地演示日程)不上报
	var grid := Game.room_id_at(npc.global_position)
	if grid == "":
		return
	# 旧日程仍可指定合并前的位置点；房间查询始终返回实际房间。
	if Game.waypoint_rooms.get(npc.goal_room, "") == grid:
		grid = npc.goal_room
	_report_queue.append({"npc": npc.npc_name, "grid": grid})
	_pump_report()


func _pump_report() -> void:
	if _reporting or _report_queue.is_empty():
		return
	_reporting = true
	var item: Dictionary = _report_queue.pop_front()
	var body := JSON.stringify({"npc": item["npc"], "grid": item["grid"]})
	_http_post.request(BACKEND + "/world/report",
		["Content-Type: application/json"], HTTPClient.METHOD_POST, body)


func _on_report_done(result: int, code: int, _headers: PackedStringArray,
		_body: PackedByteArray) -> void:
	_reporting = false
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		print("[ApiClient] 上报失败(后端可能离线), 丢弃该条")
	_pump_report()


# ---- 快进: POST /world/advance (游戏内时间控制按钮用) ----
func advance_time(minutes: int) -> void:
	if not backend_online:
		return
	var body := JSON.stringify({"minutes": minutes})
	print("[ApiClient] 快进 %d 游戏分钟..." % minutes)
	_http_adv.request(BACKEND + "/world/advance",
		["Content-Type: application/json"], HTTPClient.METHOD_POST, body)


func _on_advance_done(result: int, code: int, _headers: PackedStringArray,
		_body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		print("[ApiClient] 快进失败")
		return
	advance_done.emit()  # 下一轮轮询(≤1s)自动拿到翻段后的新目标
