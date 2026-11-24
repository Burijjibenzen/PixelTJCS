extends CanvasLayer
## 任务09的会话入口；玩家不加入NPC名册，也不暂停世界。
signal dialogue_changed(npc_name: String)
signal dialogue_layout_changed

const DIALOGUE_HEIGHT := 248.0
const DIALOGUE_MARGIN := 16.0
const DEMO_PLAYER_NAME := "同小济"

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
var dialogue_npc := ""
var dialogue_box: VBoxContainer
var identity: HBoxContainer
var help: Label
var title: Label
var portrait: TextureRect
var close_button: Button
var resize_handle: Label
var dialogue_height := DIALOGUE_HEIGHT
var resizing_dialogue := false
var resize_start_y := 0.0
var resize_start_height := 0.0
var transcript: RichTextLabel
var message_edit: TextEdit
var send_button: Button
var status: Label
var relation_label: Label
var http: HTTPRequest
var engage_http: HTTPRequest
var position_http: HTTPRequest

func display_name() -> String:
	return DEMO_PLAYER_NAME if ApiClient.web_mode else profile_name

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
	mode_button.pressed.connect(toggle_panel)
	add_child(mode_button)
	window = PanelContainer.new()
	window.name = "Window"
	window.theme = preload("res://assets/tea_theme.tres")
	window.position = Vector2(330, 86)
	window.custom_minimum_size = Vector2(540, 0)
	window.mouse_force_pass_scroll_events = false
	var style := StyleBoxFlat.new()
	style.bg_color = Color("#fff0cf")
	style.set_border_width_all(3)
	style.border_color = Color("#815432")
	style.shadow_color = Color("#3a251b70")
	style.shadow_size = 5
	style.set_corner_radius_all(6)
	style.set_content_margin_all(12)
	window.add_theme_stylebox_override("panel", style)
	add_child(window)
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 8)
	window.add_child(body)
	resize_handle = _label("拖动调整高度")
	resize_handle.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	resize_handle.mouse_filter = Control.MOUSE_FILTER_STOP
	resize_handle.mouse_default_cursor_shape = Control.CURSOR_VSIZE
	resize_handle.add_theme_color_override("font_color", Color("#815432"))
	resize_handle.add_theme_font_size_override("font_size", 12)
	resize_handle.gui_input.connect(_begin_dialogue_resize)
	body.add_child(resize_handle)
	var header := HBoxContainer.new()
	body.add_child(header)
	portrait = TextureRect.new()
	portrait.custom_minimum_size = Vector2(30, 30)
	portrait.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	portrait.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	header.add_child(portrait)
	title = _label("玩家模式")
	title.add_theme_font_size_override("font_size", 20)
	title.add_theme_color_override("font_color", Color("#69401f"))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)
	close_button = Button.new()
	close_button.focus_mode = Control.FOCUS_NONE
	close_button.pressed.connect(_close_panel)
	header.add_child(close_button)
	help = _label("WASD／方向键行走，靠近角色按 E 交谈，楼梯箭头高亮时按 E 换层，P 回到自己。")
	body.add_child(help)
	identity = HBoxContainer.new()
	body.add_child(identity)
	if ApiClient.web_mode:
		var role_label := _label(DEMO_PLAYER_NAME)
		role_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		role_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		identity.add_child(role_label)
	else:
		name_edit = LineEdit.new()
		name_edit.placeholder_text = "你的称呼"
		name_edit.text = "玩家"
		var saved := ConfigFile.new()
		if saved.load(ApiClient.PLAYER_CONFIG_PATH) == OK:
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
	dialogue_box = VBoxContainer.new()
	dialogue_box.size_flags_vertical = Control.SIZE_EXPAND_FILL
	dialogue_box.add_theme_constant_override("separation", 5)
	body.add_child(dialogue_box)
	relation_label = _label("")
	relation_label.max_lines_visible = 1
	dialogue_box.add_child(relation_label)
	transcript = RichTextLabel.new()
	transcript.bbcode_enabled = false
	transcript.custom_minimum_size.y = 64
	transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	transcript.scroll_following = true
	transcript.selection_enabled = true
	dialogue_box.add_child(transcript)
	var actions := HBoxContainer.new()
	dialogue_box.add_child(actions)
	message_edit = TextEdit.new()
	message_edit.custom_minimum_size.y = 50
	message_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	message_edit.placeholder_text = "Enter 发送 · Shift+Enter 换行"
	message_edit.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	message_edit.text_changed.connect(_update_controls)
	actions.add_child(message_edit)
	send_button = Button.new()
	send_button.text = "发送"
	send_button.pressed.connect(send)
	actions.add_child(send_button)
	status = _label("玩家模式默认关闭。填写称呼并加入后，即可开始交谈。")
	if ApiClient.web_mode:
		status.text = "有空位时可操控「同小济」，其余访客可以旁观。"
	body.add_child(status)
	_show_dialogue(false)
	_update_controls()
	window.hide()

func _label(text: String) -> Label:
	var result := Label.new()
	result.text = text
	result.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	result.add_theme_font_size_override("font_size", 14)
	result.max_lines_visible = 2
	return result

func toggle_panel() -> void:
	if window.visible:
		_close_panel()
	else:
		open_panel()

func open_panel() -> void:
	_show_dialogue(false)
	window.show()
	poll_timer = 0
	_update_controls()

func open_for(npc_name: String) -> void:
	if not active or not near_checker.is_valid() or not bool(near_checker.call(npc_name)):
		return
	if selected_npc != npc_name:
		drafts[selected_npc] = message_edit.text
		selected_npc = npc_name
		message_edit.text = str(drafts.get(npc_name, ""))
		turns = []
		relation_label.text = ""
		_render()
		history_dirty = true
	dialogue_npc = npc_name
	_set_local_hold(npc_name, true)
	engage_target = npc_name
	engage_retry_timer = 0.0
	position_timer = 0.0
	_show_dialogue(true)
	status.text = "正在请 %s 停下交谈…" % npc_name
	window.show()
	poll_timer = 0.0
	dialogue_changed.emit(npc_name)
	_update_controls()

func _show_dialogue(show_dialogue: bool) -> void:
	dialogue_box.visible = show_dialogue
	identity.visible = not show_dialogue
	help.visible = not show_dialogue
	portrait.visible = show_dialogue
	resize_handle.visible = show_dialogue
	title.text = dialogue_npc if show_dialogue else "玩家模式"
	close_button.text = "结束交谈" if show_dialogue else "关闭"
	if show_dialogue:
		for actor: NPC in actors:
			if actor.npc_name == dialogue_npc:
				portrait.texture = actor.sprite.sprite_frames.get_frame_texture("idle", 0)
				portrait.modulate = actor.sprite.modulate
				break
	else:
		window.custom_minimum_size.x = 540
		window.size = Vector2(540, 0)
		window.position = (get_viewport().get_visible_rect().size - window.size) * Vector2(0.5, 0.25)

func layout_dialogue(left_inset: float, upper_limit: float) -> void:
	if dialogue_npc.is_empty():return
	var viewport := get_viewport().get_visible_rect()
	var left := viewport.position.x + left_inset + DIALOGUE_MARGIN
	var right := viewport.end.x - 132.0
	var width := minf(960.0, right - left)
	window.custom_minimum_size.x = 0.0
	var min_height := maxf(DIALOGUE_HEIGHT, window.get_combined_minimum_size().y)
	var max_height := maxf(min_height, viewport.end.y - DIALOGUE_MARGIN - upper_limit)
	window.size = Vector2(width, clampf(dialogue_height, min_height, max_height))
	window.position = Vector2(lerpf(left, right - width, 0.5),
		viewport.end.y - DIALOGUE_MARGIN - window.size.y)

func _begin_dialogue_resize(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
		resizing_dialogue = true
		resize_start_y = event.global_position.y
		resize_start_height = window.size.y
		resize_handle.accept_event()

func _set_local_hold(npc_name: String, held: bool) -> void:
	for actor: NPC in actors:
		if actor.npc_name == npc_name:
			actor.player_chat_hold = held
			return

func _process(delta: float) -> void:
	_position_tick(delta)
	_engagement_tick(delta)
	_update_controls()
	if pending or (not window.visible and not (ApiClient.web_mode and active)):return
	if not ApiClient.backend_online:
		status.text = "后端未连接，连接后可加入和发送；草稿与已显示记录保留。"
		return
	poll_timer -= delta
	if poll_timer > 0:return
	poll_timer = 0.5 if not turn_id.is_empty() else 2.0
	if not turn_id.is_empty():
		_request("turn", "/player/turns/" + turn_id + "?session_id=" + session_id.uri_encode())
	elif history_dirty and active and not dialogue_npc.is_empty():
		history_dirty = false
		_request("history", "/player/history?session_id=" + session_id.uri_encode() + "&npc=" + selected_npc.uri_encode())
	else:
		_request("state", "/player")

func join() -> void:
	if ApiClient.web_mode:
		_request("join", "/player/join")
		return
	var name := name_edit.text.strip_edges()
	var saved := ConfigFile.new()
	var token := profile_token if name == profile_name else ""
	if saved.load(ApiClient.PLAYER_CONFIG_PATH) == OK:
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
		status.text = "请先走到所选角色同一房间的 6 格内。"
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
	var data = null if bytes.is_empty() else JSON.parse_string(bytes.get_string_from_utf8())
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
			if kind == "join" and not ApiClient.web_mode and data.get("player_token"):
				profile_name = str(data.player.name)
				profile_token = str(data.player_token)
				var saved := ConfigFile.new()
				saved.load(ApiClient.PLAYER_CONFIG_PATH)
				saved.set_value("player", "name", profile_name)
				saved.set_value("player", "token", profile_token)
				saved.set_value("player", "session_id", str(data.get("session_id", "")))
				saved.set_value("profiles", profile_name.sha256_text(), profile_token)
				saved.save(ApiClient.PLAYER_CONFIG_PATH)
			var incoming_session := str(data.session_id) if data.get("session_id") != null else ""
			var changed: bool = session_id != incoming_session
			var was_active := active
			active = bool(data.get("enabled", false)) and not incoming_session.is_empty()
			# 离开后仍可用旧会话查询最后一轮已接受的交流，直到它保存完成。
			if active:
				session_id = str(data.session_id)
				profile_name = DEMO_PLAYER_NAME if ApiClient.web_mode else str(data.player.name)
				if name_edit != null:name_edit.text = profile_name
				relations = data.get("relations", [])
				if data.get("pending_turn"):turn_id = str(data.pending_turn)
			elif kind != "leave" and data.get("pending_turn") == null:
				turn_id = ""
			ApiClient.set_player_session(session_id if active else "")
			if changed and active:
				outbox = {}
				history_dirty = true
			if kind != "state" or changed or was_active != active:
				status.text = "已加入，可以选择角色交谈。" if active else ("已有玩家会话，请在原窗口继续。" if bool(data.get("enabled", false)) else "已离开；对话记录与记忆保留。")
			if kind == "join" and active:_close_panel()
			if was_active and not active and not dialogue_npc.is_empty():_close_panel()
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
			if data.status == "processing":
				status.text = "正在保存本轮对话…" if data.get("reply") else "对方正在回复…你可以继续写下一句。"
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
	var occupied := ApiClient.web_mode and not active and bool(ApiClient.last_world.get("social", {}).get("player_occupied", false))
	var missing_name := name_edit != null and name_edit.text.strip_edges().is_empty()
	join_button.disabled = not online or pending or active or occupied or not turn_id.is_empty() or missing_name
	join_button.text = "已有玩家，正在旁观" if occupied else "加入校园"
	leave_button.disabled = not online or pending or not active
	if name_edit != null:name_edit.editable = not active
	send_button.disabled = not online or pending or not active or not turn_id.is_empty() or not _near_selected() or message_edit.text.strip_edges().is_empty() or message_edit.text.length() > 500
	send_button.text = "重试发送" if not outbox.is_empty() else "发送"
	send_button.tooltip_text = _send_block_reason()
	mode_button.text = "玩家模式 · 已加入" if active else ("旁观中" if occupied else "玩家模式")
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
		return "请结束交谈，重新走近角色并按 E。"
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
		if engage_retry_timer > 0.0:return
		release_requested = false
		_send_engagement("disengage", "")
		return
	if not engage_target.is_empty():
		if not near_checker.call(engage_target):
			_set_local_hold(engage_target, false)
			engage_target = ""
			status.text = "对方已走出交谈范围，请重新靠近。"
		elif not position_pending and position_timer > 0.0 and engage_retry_timer <= 0.0:
			_send_engagement("engage", engage_target)
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
		engage_retry_timer = 1.0
		if kind == "disengage":release_requested = true
		status.text = "交谈连接失败，请收起后重新靠近。"

func _position_tick(delta: float) -> void:
	if not active or not ApiClient.backend_online or not is_instance_valid(player_avatar):return
	position_timer -= delta
	if position_timer > 0.0 or position_pending:return
	position_timer = 0.5
	var room := Game.room_id_at(player_avatar.global_position, player_avatar.map_id)
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
	var data = null if bytes.is_empty() else JSON.parse_string(bytes.get_string_from_utf8())
	if result != HTTPRequest.RESULT_SUCCESS or code != 200 or not data is Dictionary:
		if kind == "disengage" and (result != HTTPRequest.RESULT_SUCCESS or code >= 500):
			release_requested = true
			engage_retry_timer = 1.0
		if kind == "engage":
			var still_near := near_checker.is_valid() and bool(near_checker.call(requested))
			engage_target = requested if code == 409 and window.visible and selected_npc == requested and still_near else ""
			engage_retry_timer = 1.0
			if engage_target.is_empty():
				_set_local_hold(requested, false)
		if kind == "engage" and conversation_npc == requested:conversation_npc = ""
		status.text = str(data.get("detail", "交谈连接失败，请稍后重试。")) if data is Dictionary else "交谈连接失败，请稍后重试。"
		return
	if kind == "disengage":
		conversation_npc = ""
		return
	if window.visible and selected_npc == requested and not release_requested:
		var newly_engaged := conversation_npc != requested
		conversation_npc = requested
		engage_target = ""
		engage_retry_timer = 0.0
		engage_timer = 10.0
		_set_local_hold(requested, true)
		if newly_engaged:
			status.text = "对方已停下，可以发送消息。"
			message_edit.grab_focus()
	else:
		conversation_npc = ""
		release_requested = true

func _close_panel() -> void:
	if not conversation_npc.is_empty() or not engage_kind.is_empty():release_requested = true
	conversation_npc = ""
	engage_target = ""
	window.hide()
	resizing_dialogue = false
	if not dialogue_npc.is_empty():
		_set_local_hold(dialogue_npc, false)
		dialogue_npc = ""
		dialogue_changed.emit("")

func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and message_edit.has_focus() \
			and event.keycode in [KEY_ENTER, KEY_KP_ENTER] and not message_edit.has_ime_text() \
			and not (event.ctrl_pressed or event.alt_pressed or event.meta_pressed):
		if event.shift_pressed:
			message_edit.insert_text_at_caret("\n")
		elif not event.echo and not send_button.disabled:
			send()
		get_viewport().set_input_as_handled()
	elif window.visible and event.is_action_pressed("ui_cancel") and not event.is_echo():
		_close_panel()
		get_viewport().set_input_as_handled()
	elif resizing_dialogue:
		if event is InputEventMouseMotion:
			resizing_dialogue = bool(event.button_mask & MOUSE_BUTTON_MASK_LEFT)
			if resizing_dialogue:
				dialogue_height = resize_start_height + resize_start_y - event.position.y
				dialogue_layout_changed.emit()
				dialogue_height = window.size.y
				get_viewport().set_input_as_handled()
		elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and not event.pressed:
			resizing_dialogue = false
			get_viewport().set_input_as_handled()
