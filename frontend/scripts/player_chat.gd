extends CanvasLayer
## 任务09的会话入口；玩家不加入NPC名册，也不暂停世界。
var actors: Array = []
var player_avatar: PlayerAvatar
var backend_url := ""
var active := false
var session_id := ""
var profile_token := ""
var profile_name := ""
var near_checker: Callable
var selected_npc := ""
var conversation_npc := ""
var engage_target := ""
var engage_sent_npc := ""
var engage_kind := ""
var engage_timer := 0.0
var engage_retry_timer := 0.0
var position_timer := 0.0
var position_pending := false
var release_requested := false
var turn_id := ""
var request_kind := ""
var request_npc := ""
var pending := false
var history_dirty := false
var poll_timer := 0.0
var outbox := {}
var drafts := {}
var turns: Array = []
var relations: Array = []
var submitted_text := ""
var mode_button: Button
var window: PanelContainer
var name_edit: LineEdit
var join_button: Button
var leave_button: Button
var picker: OptionButton
var transcript: RichTextLabel
var message_edit: TextEdit
var send_button: Button
var send_hint: Label
var status: Label
var relation_label: Label
var http: HTTPRequest
var engage_http: HTTPRequest
var position_http: HTTPRequest

func setup(npcs: Array) -> void:
	actors = npcs

func _ready() -> void:
	layer = 21
	backend_url = ApiClient.backend_url
	http = HTTPRequest.new()
	http.timeout = 8.0
	add_child(http)
	http.request_completed.connect(_done)
	engage_http = HTTPRequest.new()
	engage_http.timeout = 8.0
	add_child(engage_http)
	engage_http.request_completed.connect(_engage_done)
	position_http = HTTPRequest.new()
	position_http.timeout = 3.0
	add_child(position_http)
	position_http.request_completed.connect(_position_done)
	mode_button = Button.new()
	mode_button.text = "玩家模式"
	mode_button.position = Vector2(670, 12)
	mode_button.pressed.connect(open_panel)
	add_child(mode_button)
	window = PanelContainer.new()
	window.name = "Window"
	window.position = Vector2(330, 60)
	window.custom_minimum_size = Vector2(620, 610)
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.055, 0.085, 0.13, 0.98)
	style.set_corner_radius_all(10)
	style.set_content_margin_all(16)
	window.add_theme_stylebox_override("panel", style)
	add_child(window)
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 8)
	window.add_child(body)
	var header := HBoxContainer.new()
	body.add_child(header)
	var title := _label("玩家模式 · 校园交谈")
	title.add_theme_font_size_override("font_size", 22)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)
	var close := Button.new()
	close.text = "收起"
	close.pressed.connect(_close_panel)
	header.add_child(close)
	body.add_child(_label("加入后用 WASD／方向键行走，靠近角色按 E 交谈；楼梯旁按 E 换层，P 回到自己。"))
	var identity := HBoxContainer.new()
	body.add_child(identity)
	name_edit = LineEdit.new()
	name_edit.placeholder_text = "你的称呼"
	name_edit.text = "玩家"
	var saved := ConfigFile.new()
	if saved.load("user://roamchestra-player.cfg") == OK:
		profile_name = str(saved.get_value("player", "name", ""))
		profile_token = str(saved.get_value("player", "token", ""))
		session_id = str(saved.get_value("player", "session_id", ""))
		if not profile_name.is_empty():name_edit.text = profile_name
	name_edit.max_length = 24
	name_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	identity.add_child(name_edit)
	join_button = Button.new()
	join_button.text = "加入校园"
	join_button.pressed.connect(join)
	identity.add_child(join_button)
	leave_button = Button.new()
	leave_button.text = "离开玩家模式"
	leave_button.pressed.connect(leave)
	identity.add_child(leave_button)
	picker = OptionButton.new()
	for actor in actors:picker.add_item(actor.npc_name)
	if picker.item_count > 0:selected_npc = picker.get_item_text(0)
	picker.item_selected.connect(_choose)
	body.add_child(picker)
	relation_label = _label("加入后可查看与该角色的关系。")
	body.add_child(relation_label)
	transcript = RichTextLabel.new()
	transcript.bbcode_enabled = false
	transcript.custom_minimum_size.y = 185
	transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	transcript.scroll_following = true
	transcript.selection_enabled = true
	body.add_child(transcript)
	message_edit = TextEdit.new()
	message_edit.custom_minimum_size.y = 76
	message_edit.placeholder_text = "想对TA说些什么？最多500字，点击发送。"
	message_edit.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	message_edit.text_changed.connect(_update_controls)
	body.add_child(message_edit)
	send_hint = _label("先走近角色，按 E 发起交谈。")
	send_hint.add_theme_color_override("font_color", Color("#ffd86b"))
	body.add_child(send_hint)
	var actions := HBoxContainer.new()
	body.add_child(actions)
	send_button = Button.new()
	send_button.text = "发送"
	send_button.pressed.connect(send)
	actions.add_child(send_button)
	status = _label("玩家模式默认关闭。填写称呼并加入后，即可开始交谈。")
	status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	actions.add_child(status)
	_update_controls()
	window.hide()

func _label(text: String) -> Label:
	var result := Label.new()
	result.text = text
	result.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	result.add_theme_font_size_override("font_size", 14)
	result.max_lines_visible = 2
	return result

func open_panel() -> void:
	window.show()
	poll_timer = 0
	_update_controls()

func open_for(npc_name: String) -> void:
	for index in picker.item_count:
		if picker.get_item_text(index) == npc_name:
			_choose(index)
			picker.select(index)
			break
	if conversation_npc != npc_name:
		if not conversation_npc.is_empty():release_requested = true
		conversation_npc = ""
	if active and near_checker.is_valid() and bool(near_checker.call(npc_name)):
		engage_target = npc_name
		status.text = "正在请角色停下交谈…"
	else:
		engage_target = ""
	open_panel()

func _choose(index: int) -> void:
	if selected_npc == picker.get_item_text(index):return
	if not conversation_npc.is_empty():release_requested = true
	conversation_npc = ""
	drafts[selected_npc] = message_edit.text
	selected_npc = picker.get_item_text(index)
	engage_target = selected_npc if active and near_checker.is_valid() and bool(near_checker.call(selected_npc)) else ""
	message_edit.text = str(drafts.get(selected_npc, ""))
	turns = []
	_render()
	history_dirty = active
	_update_controls()

func _process(delta: float) -> void:
	_position_tick(delta)
	_engagement_tick(delta)
	_update_controls()
	if not window.visible or pending:return
	if not ApiClient.backend_online:
		status.text = "后端未连接，连接后可加入和发送；草稿与已显示记录保留。"
		return
	poll_timer -= delta
	if poll_timer > 0:return
	poll_timer = 0.5 if not turn_id.is_empty() else 2.0
	if not turn_id.is_empty():
		_request("turn", "/player/turns/" + turn_id + "?session_id=" + session_id.uri_encode())
	elif history_dirty and active:
		history_dirty = false
		_request("history", "/player/history?session_id=" + session_id.uri_encode() + "&npc=" + selected_npc.uri_encode())
	else:
		_request("state", "/player")

func join() -> void:
	var name := name_edit.text.strip_edges()
	var saved := ConfigFile.new()
	var token := profile_token if name == profile_name else ""
	if saved.load("user://roamchestra-player.cfg") == OK:
		token = str(saved.get_value("profiles", name.sha256_text(), token))
	_request("join", "/player/join", {"name": name, "session_id": session_id,
		"player_token": token})

func leave() -> void:
	conversation_npc = ""
	engage_target = ""
	release_requested = false
	_request("leave", "/player/leave", {"session_id": session_id})

func send() -> void:
	if pending or not active or not turn_id.is_empty():return
	if not _near_selected():
		status.text = "请先走到所选角色身边（约 3 格）。"
		return
	var message := message_edit.text.strip_edges()
	if message.is_empty() or message.length() > 500:return
	# 网络超时后重试相同草稿沿用请求编号，不能再生成一轮记忆或关系变化。
	if outbox.get("message") != message or outbox.get("npc") != selected_npc or outbox.get("session_id") != session_id:
		outbox = {"session_id": session_id, "npc": selected_npc, "message": message,
			"request_id": Crypto.new().generate_random_bytes(16).hex_encode()}
	submitted_text = message_edit.text
	_request("send", "/player/chat", outbox)

func _request(kind: String, path: String, payload := {}) -> void:
	if pending or not ApiClient.backend_online:return
	pending = true
	request_kind = kind
	request_npc = selected_npc
	var method := HTTPClient.METHOD_GET if kind in ["state", "turn", "history"] else HTTPClient.METHOD_POST
	var headers := ["Content-Type: application/json"]
	if kind == "state" and not session_id.is_empty():
		headers.append("X-Player-Session: " + session_id)
	var error := http.request(backend_url + path, headers, method,
		"" if method == HTTPClient.METHOD_GET else JSON.stringify(payload))
	if error != OK:
		pending = false
		status.text = "连接未能建立，请稍后重试。"
	_update_controls()

func _done(result: int, code: int, _headers: PackedStringArray, bytes: PackedByteArray) -> void:
	var kind := request_kind
	pending = false
	var data = JSON.parse_string(bytes.get_string_from_utf8())
	if result != HTTPRequest.RESULT_SUCCESS or code not in [200, 202] or not data is Dictionary:
		status.text = str(data.get("detail", "请求失败，请稍后重试。")) if data is Dictionary else "连接暂时中断；草稿已保留，可重试发送。"
		if kind == "history":history_dirty = true
		if kind == "turn" and code in [404, 409]:
			turn_id = ""
			poll_timer = 0
		_update_controls()
		return
	match kind:
		"state", "join", "leave":
			if kind == "join" and data.get("player_token"):
				profile_name = str(data.player.name)
				profile_token = str(data.player_token)
				var saved := ConfigFile.new()
				saved.load("user://roamchestra-player.cfg")
				saved.set_value("player", "name", profile_name)
				saved.set_value("player", "token", profile_token)
				saved.set_value("player", "session_id", str(data.get("session_id", "")))
				saved.set_value("profiles", profile_name.sha256_text(), profile_token)
				saved.save("user://roamchestra-player.cfg")
			var incoming_session := str(data.session_id) if data.get("session_id") != null else ""
			var changed: bool = session_id != incoming_session
			var was_active := active
			active = bool(data.get("enabled", false)) and not incoming_session.is_empty()
			# 离开后仍可用旧会话查询最后一轮已接受的交流，直到它保存完成。
			if active:
				session_id = str(data.session_id)
				name_edit.text = str(data.player.name)
				relations = data.get("relations", [])
				if data.get("pending_turn"):turn_id = str(data.pending_turn)
			elif kind != "leave" and data.get("pending_turn") == null:
				turn_id = ""
			if changed and active:
				outbox = {}
				history_dirty = true
			if kind != "state" or changed or was_active != active:
				status.text = "已加入，可以选择角色交谈。" if active else ("已有玩家会话，请在原窗口继续。" if bool(data.get("enabled", false)) else "已离开；对话记录与记忆保留。")
			if kind == "join" and active:_close_panel()
			if not active and not turn_id.is_empty():status.text = "已离开，最后一轮回复仍会完成并保存。"
		"history":
			var items: Array = data.get("turns", [])
			# 切换角色期间返回的旧历史不能覆盖当前角色。
			if request_npc == selected_npc:
				turns = items
				_render()
		"send", "turn":
			if kind == "send":
				if data.npc == selected_npc and message_edit.text == submitted_text:
					message_edit.clear()
					drafts[selected_npc] = ""
				elif data.npc != selected_npc and drafts.get(str(data.npc)) == submitted_text:
					drafts[str(data.npc)] = ""
				outbox = {}
			turn_id = str(data.id) if data.status == "processing" else ""
			if data.npc == selected_npc:
				var index := -1
				for row in turns.size():
					if turns[row].id == data.id:index = row
				if index < 0:turns.append(data)
				else:turns[index] = data
				_render()
			if data.status == "processing":status.text = "对方正在回复…你可以继续写下一句。"
			elif data.status == "failed":status.text = str(data.error)
			else:
				status.text = "本轮交谈已保存。"
				history_dirty = active
			poll_timer = 0.5
	_update_controls()

func _render() -> void:
	var text := ""
	for turn: Dictionary in turns:
		if turn.get("transcript", []).is_empty():
			text += "你：" + str(turn.message) + "\n\n"
		else:
			for line: Dictionary in turn.transcript:text += str(line.speaker) + "：" + str(line.text) + "\n\n"
		if turn.get("relation"):
			var info: Dictionary = turn.relation
			relation_label.text = "好感度 %.0f / 100（%+.0f）· %s" % [info.score, info.delta, info.reason]
	if transcript.text != text:transcript.text = text

func _update_controls() -> void:
	if not is_instance_valid(send_button):return
	var online := ApiClient.backend_online
	join_button.disabled = not online or pending or active or not turn_id.is_empty() or name_edit.text.strip_edges().is_empty()
	leave_button.disabled = not online or pending or not active
	if name_edit.editable == active:name_edit.editable = not active
	picker.disabled = not turn_id.is_empty() or (pending and request_kind == "send")
	send_button.disabled = not online or pending or not active or not turn_id.is_empty() or not _near_selected() or message_edit.text.strip_edges().is_empty() or message_edit.text.length() > 500
	send_button.text = "重试发送" if not outbox.is_empty() else "发送"
	send_hint.text = _send_block_reason()
	send_button.tooltip_text = send_hint.text
	mode_button.text = "玩家模式 · 已加入" if active else "玩家模式"
	if turns.is_empty():
		for edge: Dictionary in relations:
			if edge.npc == selected_npc:relation_label.text = "好感度 %.0f / 100 · %s" % [edge.score, edge.reason]

func _send_block_reason() -> String:
	if not ApiClient.backend_online:return "后端未连接，暂时无法发送。"
	if pending:return "正在处理请求，请稍候。"
	if not active:return "请先点击「加入校园」。"
	if not turn_id.is_empty():return "上一轮仍在回复，请等回复完成。"
	if engage_kind == "engage" or not engage_target.is_empty():return "正在等待角色停下交谈。"
	if conversation_npc != selected_npc or conversation_npc.is_empty():
		return "请收起面板，走到所选角色同一房间的约 3 格内，再按 E。"
	if not near_checker.is_valid() or not bool(near_checker.call(selected_npc)):
		return "已离开交谈范围，请重新靠近角色按 E。"
	if message_edit.text.strip_edges().is_empty():return "先输入想说的话。"
	if message_edit.text.length() > 500:return "消息最多 500 字。"
	return "可以发送。"

func _near_selected() -> bool:
	return window.visible and not conversation_npc.is_empty() and conversation_npc == selected_npc \
		and near_checker.is_valid() and bool(near_checker.call(selected_npc))

func _engagement_tick(delta: float) -> void:
	engage_retry_timer = maxf(0.0, engage_retry_timer - delta)
	if not active or not ApiClient.backend_online or not engage_kind.is_empty():return
	if release_requested:
		release_requested = false
		_send_engagement("disengage", "")
		return
	if not engage_target.is_empty():
		if engage_retry_timer <= 0.0:_send_engagement("engage", engage_target)
		return
	if window.visible and not conversation_npc.is_empty():
		engage_timer -= delta
		if engage_timer <= 0.0:_send_engagement("engage", conversation_npc)

func _send_engagement(kind: String, npc_name: String) -> void:
	engage_kind = kind
	engage_sent_npc = npc_name
	var payload := {"session_id": session_id}
	if kind == "engage":payload["npc"] = npc_name
	var error := engage_http.request(backend_url + "/player/" + kind,
		["Content-Type: application/json"], HTTPClient.METHOD_POST, JSON.stringify(payload))
	if error != OK:
		engage_kind = ""
		status.text = "交谈连接失败，请收起后重新靠近。"

func _position_tick(delta: float) -> void:
	if not active or not ApiClient.backend_online or not is_instance_valid(player_avatar):return
	position_timer -= delta
	if position_timer > 0.0 or position_pending:return
	position_timer = 0.5
	var room := Game.room_id_at(player_avatar.global_position, player_avatar.map_id)
	if room.is_empty():return
	var local: Vector2 = player_avatar.global_position - Game.map_offset(player_avatar.map_id)
	position_pending = true
	if position_http.request(backend_url + "/player/position", ["Content-Type: application/json"],
		HTTPClient.METHOD_POST, JSON.stringify({"session_id":session_id,"map_id":player_avatar.map_id,
			"room_id":room,"x":local.x,"y":local.y})) != OK:position_pending = false

func _position_done(_result: int, _code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
	position_pending = false

func _engage_done(result: int, code: int, _headers: PackedStringArray, bytes: PackedByteArray) -> void:
	var kind := engage_kind
	var requested := engage_sent_npc
	engage_kind = ""
	var data = JSON.parse_string(bytes.get_string_from_utf8())
	if result != HTTPRequest.RESULT_SUCCESS or code != 200 or not data is Dictionary:
		if kind == "engage":
			var still_near := near_checker.is_valid() and bool(near_checker.call(requested))
			engage_target = requested if code == 409 and window.visible and selected_npc == requested and still_near else ""
			engage_retry_timer = 1.0
		if kind == "engage" and conversation_npc == requested:conversation_npc = ""
		status.text = str(data.get("detail", "交谈连接失败，请稍后重试。")) if data is Dictionary else "交谈连接失败，请稍后重试。"
		return
	if kind == "disengage":
		conversation_npc = ""
		return
	if window.visible and selected_npc == requested and not release_requested:
		conversation_npc = requested
		engage_target = ""
		engage_retry_timer = 0.0
		engage_timer = 10.0
		for actor in actors:
			if actor.npc_name == requested:actor.social_busy = true
		status.text = "对方已停下，可以发送消息。"
	else:
		conversation_npc = ""
		release_requested = true

func _close_panel() -> void:
	if not conversation_npc.is_empty() or not engage_kind.is_empty():release_requested = true
	conversation_npc = ""
	engage_target = ""
	window.hide()

func _unhandled_key_input(event: InputEvent) -> void:
	if window.visible and event.is_action_pressed("ui_cancel"):
		_close_panel()
		get_viewport().set_input_as_handled()
