extends CanvasLayer
## 任务09的会话入口；玩家不加入NPC名册，也不暂停世界。
var actors: Array = []
var backend_url := ""
var active := false
var session_id := ""
var selected_npc := ""
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
var status: Label
var relation_label: Label
var http: HTTPRequest

func setup(npcs: Array) -> void:
	actors = npcs

func _ready() -> void:
	layer = 21
	backend_url = ApiClient.backend_url
	http = HTTPRequest.new()
	http.timeout = 8.0
	add_child(http)
	http.request_completed.connect(_done)
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
	close.pressed.connect(func(): window.hide())
	header.add_child(close)
	body.add_child(_label("选择角色交谈，也可点击地图人物切换；世界继续运行。"))
	var identity := HBoxContainer.new()
	body.add_child(identity)
	name_edit = LineEdit.new()
	name_edit.placeholder_text = "你的称呼"
	name_edit.text = "玩家"
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
	open_panel()

func _choose(index: int) -> void:
	if selected_npc == picker.get_item_text(index):return
	drafts[selected_npc] = message_edit.text
	selected_npc = picker.get_item_text(index)
	message_edit.text = str(drafts.get(selected_npc, ""))
	turns = []
	_render()
	history_dirty = active
	_update_controls()

func _process(delta: float) -> void:
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
	_request("join", "/player/join", {"name": name_edit.text.strip_edges()})

func leave() -> void:
	_request("leave", "/player/leave", {"session_id": session_id})

func send() -> void:
	if pending or not active or not turn_id.is_empty():return
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
	var error := http.request(backend_url + path, ["Content-Type: application/json"], method,
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
			var incoming_session := str(data.session_id) if data.get("session_id") != null else ""
			var changed: bool = session_id != incoming_session
			active = bool(data.get("enabled", false))
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
			status.text = "已加入，可以选择角色交谈。" if active else "已离开；对话记录与记忆保留。"
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
	send_button.disabled = not online or pending or not active or not turn_id.is_empty() or message_edit.text.strip_edges().is_empty() or message_edit.text.length() > 500
	send_button.text = "重试发送" if not outbox.is_empty() else "发送"
	mode_button.text = "玩家模式 · 已加入" if active else "玩家模式"
	if turns.is_empty():
		for edge: Dictionary in relations:
			if edge.npc == selected_npc:relation_label.text = "好感度 %.0f / 100 · %s" % [edge.score, edge.reason]

func _unhandled_key_input(event: InputEvent) -> void:
	if window.visible and event.is_action_pressed("ui_cancel"):
		window.hide()
		get_viewport().set_input_as_handled()
