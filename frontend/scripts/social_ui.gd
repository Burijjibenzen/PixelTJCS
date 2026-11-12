extends CanvasLayer
## M3 独立观察界面：WebSocket 事件流、分组并发气泡、记忆/关系面板。
# M3: 每段最多十秒；新台词立即替换，长文本按标点和长度拆段。
const BUBBLE_SECONDS := 10.0
const SEGMENT_CHARS := 60
var actors: Array = []
var cursor := 0
var epoch := ""
# M3: 删除 HTTP 社交轮询，使用文档规定的 /ws/events。
var socket := WebSocketPeer.new()
var reconnect_delay := 0.0
var socket_age := 0.0
var ping_timer := 0.0
var stream_online := false
var history_http: HTTPRequest
var history_pending := false
var history_name := ""
var history_items: Array = []
var history_slider: HSlider
var history_label: Label
var relation_grid: GridContainer
var dialogue_log: RichTextLabel
var panel_http: HTTPRequest
var control_http: HTTPRequest
var panel_timer := 0.0
var panel_pending := false
var partner: OptionButton
var picker: OptionButton
var tabs: TabContainer
var views: Array[RichTextLabel] = []
var control_pending := false
var model_status: Label
var status: Label
var panel: PanelContainer
var paused := false
var fetching := "memory"
var panel_name := ""
var bubble_labels := {}
# M3: 每场对话单独计时，允许不同角色组同时播放。
var playbacks: Array = []
# M3 修复：保存正在生成的最新一句，重连后恢复，完成事件不重复播整场。
var live_versions := {}
# M3: 快照和断线补发可能包含同一句，以对话 ID/句序去重日志。
var logged_lines := {}
var meeting_status: Label
var notice := ""
var notice_until := 0.0

func setup(npcs: Array) -> void:
	actors = npcs

func _ready() -> void:
	layer = 10
	history_http = HTTPRequest.new()
	history_http.timeout = 5
	add_child(history_http)
	history_http.request_completed.connect(_history_done)
	panel_http = HTTPRequest.new()
	panel_http.timeout = 5
	add_child(panel_http)
	panel_http.request_completed.connect(_panel_done)
	control_http = HTTPRequest.new()
	control_http.request_completed.connect(_control_done)
	control_http.timeout = 5
	add_child(control_http)
	var open := Button.new()
	open.text = "社交观察 / 收起"
	open.position = Vector2(450, 12)
	add_child(open)
	panel = PanelContainer.new()
	panel.position = Vector2(760, 55)
	panel.size = Vector2(500, 530)
	# 使用不透明背景，避免原日程面板和地图穿透影响阅读。
	var background := StyleBoxFlat.new()
	background.bg_color = Color(0.08, 0.10, 0.14, 1.0)
	background.content_margin_left = 12
	background.content_margin_right = 12
	background.content_margin_top = 12
	background.content_margin_bottom = 12
	background.corner_radius_top_left = 8
	background.corner_radius_bottom_left = 8
	panel.add_theme_stylebox_override("panel", background)
	add_child(panel)
	open.pressed.connect(func(): panel.visible = not panel.visible)
	var box := VBoxContainer.new()
	panel.add_child(box)
	status = Label.new()
	status.text = "等待后端连接"
	status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status.custom_minimum_size = Vector2(440, 42)
	box.add_child(status)
	model_status = Label.new()
	box.add_child(model_status)
	var controls := HBoxContainer.new()
	box.add_child(controls)
	var pause := Button.new()
	pause.text = "暂停 / 继续调度"
	controls.add_child(pause)
	pause.pressed.connect(func():
		var desired := not bool(ApiClient.last_world.get("social", {}).get("paused", false))
		_send_control("/social/control", {"paused": desired, "speed": ApiClient.last_world.get("social", {}).get("speed", 10)})
	)
	var advance := Button.new()
	advance.text = "快进3天"
	controls.add_child(advance)
	advance.pressed.connect(func(): ApiClient.advance_time(4320))
	picker = OptionButton.new()
	for npc in actors:
		picker.add_item(npc.npc_name)
	box.add_child(picker)
	var meet_row := HBoxContainer.new()
	box.add_child(meet_row)
	partner = OptionButton.new()
	for npc in actors:
		partner.add_item(npc.npc_name)
	if partner.item_count > 1:
		partner.select(1)
	meet_row.add_child(partner)
	var meet := Button.new()
	meet.text = "安排与所选角色步行会面"
	meet_row.add_child(meet)
	meet.pressed.connect(func():
		var first := picker.get_item_text(picker.selected)
		var second := partner.get_item_text(partner.selected)
		if first == second:
			_notice("请选择两个不同角色，不能安排自己与自己会面")
			return
		_send_control("/social/meet", {"a": first, "b": second})
	)
	var cancel := Button.new()
	cancel.text = "取消所选两人的待执行会面"
	box.add_child(cancel)
	cancel.pressed.connect(func():
		var a := picker.get_item_text(picker.selected)
		var b := partner.get_item_text(partner.selected)
		for m in ApiClient.last_world.get("social", {}).get("meetings", []):
			if a in [m.a, m.b] and b in [m.a, m.b] and a != b and m.state in ["waiting", "moving", "ready", "cooldown"]:
				_send_control("/social/meet/" + str(m.id) + "/cancel", {})
				return
		_notice("所选两人没有可取消的待执行会面")
	)
	meeting_status = Label.new()
	meeting_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	meeting_status.max_lines_visible = 3
	box.add_child(meeting_status)
	picker.item_selected.connect(func(_i):
		panel_timer = 0
		history_items.clear()
		history_slider.max_value = 0
		_fetch_history()
	)
	history_label = Label.new()
	history_label.text = "记忆时间线：当前"
	box.add_child(history_label)
	history_slider = HSlider.new()
	history_slider.step = 1
	history_slider.max_value = 0
	box.add_child(history_slider)
	history_slider.value_changed.connect(func(_value): panel_timer = 0)
	tabs = TabContainer.new()
	tabs.custom_minimum_size = Vector2(480, 250)
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(tabs)
	for title in ["活跃记忆", "遗忘档案", "反思洞察", "关系"]:
		var view := RichTextLabel.new()
		view.bbcode_enabled = true
		view.scroll_active = true
		if title == "关系":
			var scroll := ScrollContainer.new()
			scroll.name = title
			tabs.add_child(scroll)
			var relation_box := VBoxContainer.new()
			scroll.add_child(relation_box)
			relation_grid = GridContainer.new()
			relation_box.add_child(relation_grid)
			view.custom_minimum_size = Vector2(450, 250)
			relation_box.add_child(view)
		else:
			view.name = title
			tabs.add_child(view)
		views.append(view)
	dialogue_log = RichTextLabel.new()
	dialogue_log.name = "对话日志"
	dialogue_log.bbcode_enabled = false
	# M3: 新台词到达时跟随日志末尾，避免停在旧内容误以为未收到。
	dialogue_log.scroll_following = true
	tabs.add_child(dialogue_log)
	tabs.tab_changed.connect(func(_i): panel_timer = 0)

func _process(delta: float) -> void:
	_poll_socket(delta)
	for playback in playbacks.duplicate():
		playback.remaining -= delta
		if playback.remaining <= 0:
			_advance_turn(playback)
	var meeting_lines: PackedStringArray = []
	var selected := picker.get_item_text(picker.selected)
	for m in ApiClient.last_world.get("social", {}).get("meetings", []):
		if selected in [m.a, m.b]:
			meeting_lines.append("%s ↔ %s：%s" % [m.a, m.b, m.detail])
	meeting_status.text = "\n".join(meeting_lines.slice(maxi(0, meeting_lines.size() - 3)))
	model_status.text = "模型已配置" if ApiClient.last_world.get("social", {}).get("model_configured", false) else "预设台词模式：未配置模型密钥"
	panel_timer -= delta
	status.text = notice if Time.get_ticks_msec() / 1000.0 < notice_until else ("调度已暂停 · " if ApiClient.last_world.get("social", {}).get("paused", false) else "自主社交 · ") + ApiClient.backend_time
	if not stream_online:
		status.text = "对话事件流重连中 · " + ApiClient.backend_time
	if not ApiClient.backend_online:
		status.text = "WebSocket 重连中 · 世界接口暂未连接"
		return
	if panel.visible and panel_timer <= 0 and not panel_pending and picker.item_count > 0 and tabs.current_tab != 4:
		panel_timer = 2
		panel_pending = true
		panel_name = picker.get_item_text(picker.selected)
		fetching = "relations" if tabs.current_tab == 3 else "memory"
		var url := "/social/relations" if fetching == "relations" else "/npcs/" + panel_name.uri_encode() + "/memory"
		if fetching == "memory":
			var index := int(history_slider.value)
			if index < history_items.size():
				url += "?snapshot_id=" + str(int(history_items[index].id))
				history_label.text = "历史快照：第 %.0f 游戏分钟" % history_items[index].game_time
			else:
				history_label.text = "记忆时间线：当前"
		if panel_http.request(ApiClient.BACKEND + url) != OK:
			panel_pending = false

# M3: WebSocketPeer.poll 是收取持久连接的数据，不是 HTTP 定时查询。
func _poll_socket(delta: float) -> void:
	socket.poll()
	reconnect_delay -= delta
	socket_age += delta
	ping_timer -= delta
	var state := socket.get_ready_state()
	if state == WebSocketPeer.STATE_CLOSED:
		stream_online = false
		if reconnect_delay <= 0:
			reconnect_delay = 2
			socket = WebSocketPeer.new()
			# M3: 首次连接也请求持久事件，避免后台已生成的台词永远缺失。
			var url := ApiClient.BACKEND.replace("http://", "ws://") + "/ws/events?after=" + str(cursor)
			var error := socket.connect_to_url(url)
			if error != OK:
				_notice("对话事件连接失败：" + error_string(error))
			socket_age = 0
		return
	if state == WebSocketPeer.STATE_OPEN:
		if ping_timer <= 0:
			ping_timer = 2
			socket.send_text(JSON.stringify({"kind": "ping"}))
		while socket.get_available_packet_count() > 0:
			var message = JSON.parse_string(socket.get_packet().get_string_from_utf8())
			if message is Dictionary:
				socket_age = 0
				stream_online = true
				_accept_stream(message)
	if socket_age > 12:
		socket.close()

func _accept_stream(message: Dictionary) -> void:
	if message.kind == "heartbeat":
		return
	if message.kind == "snapshot":
		# M3: 后端重启可能重置事件编号，不能沿用旧对话的显示去重状态。
		if epoch != "" and epoch != str(message.epoch):
			logged_lines.clear()
		epoch = str(message.epoch)
		# M3: 快照的 ID 可能是服务端最新值；不能提前跳过尚未收到的历史台词。
		cursor = mini(cursor, int(message.id))
		playbacks.clear()
		live_versions.clear()
		for npc in actors:
			npc.social_playing = false
		for bubble in bubble_labels.values():
			bubble.hide()
		ApiClient.apply_stream_world(message.world)
		for line in message.get("live", []):
			_show_line(line)
			# M3: 日志交给随后按 ID 排序的事件补发，避免最新句插到旧句前面。
		_fetch_history()
		return
	var event_id := int(message.get("id", 0))
	if event_id <= cursor:
		return
	cursor = event_id
	match str(message.kind):
		"world":
			if not message.get("replay", false):
				ApiClient.apply_stream_world(message.snapshot)
		"dialogue_line":
			_append_dialogue(message)
			if not message.get("replay", false):
				_show_line(message)
		"memory_changed", "relation_changed":
			panel_timer = 0
			_fetch_history()
		"conversation_released", "error":
			for playback in playbacks:
				if playback.event.get("conversation_id", "") == message.get("conversation_id", ""):
					playback.index = playback.event.result.transcript.size()
					playback.remaining = 0
			if message.kind == "error":
				_notice(str(message.message))

# M3: 事件补发只补日志；地图仍按服务端活跃快照和实时台词显示，避免重演旧聊天。
func _append_dialogue(line: Dictionary) -> void:
	var key := "%s:%s" % [line.conversation_id, int(line.sequence)]
	if logged_lines.has(key):
		return
	logged_lines[key] = true
	dialogue_log.text += "%s：%s\n\n" % [line.speaker, line.text]
	if dialogue_log.text.length() > 16000:
		dialogue_log.text = dialogue_log.text.right(12000)

func _show_line(line: Dictionary) -> void:
	var key := str(line.conversation_id)
	if int(live_versions.get(key, -1)) >= int(line.sequence):
		return
	live_versions[key] = int(line.sequence)
	# 新句替换当前段；也清理同一角色上一场残余气泡，避免两场互相释放移动锁。
	for old in playbacks.duplicate():
		if old.event.participants.any(func(n): return n in line.participants):
			for npc in actors:
				if npc.npc_name in old.event.participants:
					npc.social_playing = false
					if bubble_labels.has(npc.npc_name):
						bubble_labels[npc.npc_name].hide()
			playbacks.erase(old)
	_start_playback({"conversation_id": key, "participants": line.participants,
		"result": {"transcript": [{"speaker": line.speaker, "text": line.text}]}})

func _fetch_history() -> void:
	if history_pending or picker.item_count == 0:
		return
	history_name = picker.get_item_text(picker.selected)
	history_pending = true
	if history_http.request(ApiClient.BACKEND + "/npcs/" + history_name.uri_encode() + "/memory/history") != OK:
		history_pending = false

func _history_done(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	history_pending = false
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		return
	if history_name != picker.get_item_text(picker.selected):
		_fetch_history()
		return
	var data = JSON.parse_string(body.get_string_from_utf8())
	if data is Array:
		var was_current := int(history_slider.value) >= history_items.size()
		history_items = data
		history_slider.max_value = history_items.size()
		if was_current:
			history_slider.value = history_items.size()

func _draw_relations(edges: Array) -> void:
	for child in relation_grid.get_children():
		relation_grid.remove_child(child)
		child.queue_free()
	var names: Array[String] = []
	for npc in actors:
		names.append(npc.npc_name)
	relation_grid.columns = names.size() + 1
	for row in range(-1, names.size()):
		for column in range(-1, names.size()):
			var cell := Label.new()
			cell.custom_minimum_size = Vector2(38, 26)
			cell.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
			cell.add_theme_font_size_override("font_size", 11)
			if row < 0 or column < 0:
				cell.text = "" if row == column else names[maxi(row, column)]
			elif row == column:
				cell.text = "—"
			else:
				for edge in edges:
					if names[row] in [edge.a, edge.b] and names[column] in [edge.a, edge.b]:
						cell.text = "%.0f" % edge.score
						cell.tooltip_text = "%s ↔ %s：%s；%s" % [edge.a, edge.b, edge.level, edge.reason]
						var style := StyleBoxFlat.new()
						style.bg_color = Color(0.55, 0.15, 0.15).lerp(Color(0.10, 0.55, 0.30), float(edge.score) / 100)
						cell.add_theme_stylebox_override("normal", style)
			relation_grid.add_child(cell)

# M3: 不再使用全局队列；每组只清理自己的气泡和移动锁。
func _segments(text: String) -> Array[String]:
	var segments: Array[String] = []
	var part := ""
	for character in text:
		part += character
		if part.length() >= SEGMENT_CHARS or (part.length() >= 30 and character in "。！？；\n"):
			if not part.strip_edges().is_empty():
				segments.append(part.strip_edges())
			part = ""
	if not part.strip_edges().is_empty():
		segments.append(part.strip_edges())
	return segments

func _start_playback(event: Dictionary) -> void:
	# M3: 复制事件再拆段；不得改写服务端序号和原始台词。
	var display := event.duplicate(true)
	var lines: Array = []
	for line in event.result.transcript:
		for segment in _segments(str(line.text)):
			lines.append({"speaker": line.speaker, "text": segment})
	display.result.transcript = lines
	var playback := {"event": display, "index": 0, "remaining": 0.0}
	playbacks.append(playback)
	for npc in actors:
		if npc.npc_name in event.participants:
			npc.social_playing = true
	_advance_turn(playback)

# M3: 气泡底边固定在姓名/头部上方，内容变高时向上伸展，不遮挡脸。
func _place_bubble(npc: Node2D, bubble: Control) -> void:
	var head_y := -88.0
	var sprite := npc.get_node_or_null("Sprite") as AnimatedSprite2D
	if sprite and sprite.sprite_frames:
		var texture := sprite.sprite_frames.get_frame_texture(sprite.animation, sprite.frame)
		if texture:
			head_y = minf(head_y, sprite.position.y - texture.get_height() * sprite.scale.y / 2.0 - 12.0)
	bubble.position = Vector2(-bubble.size.x / 2.0, head_y - bubble.size.y)

func _advance_turn(playback: Dictionary) -> void:
	var event: Dictionary = playback.event
	for name in event.participants:
		if bubble_labels.has(name):
			bubble_labels[name].hide()
	if playback.index >= event.result.transcript.size():
		for npc in actors:
			if npc.npc_name in event.participants:
				npc.social_playing = false
		playbacks.erase(playback)
		panel_timer = 0
		return
	var line: Dictionary = event.result.transcript[playback.index]
	playback.index += 1
	playback.remaining = BUBBLE_SECONDS
	for npc in actors:
		if npc.npc_name != line.speaker:
			continue
		if not bubble_labels.has(npc.npc_name):
			var bubble := PanelContainer.new()
			bubble.size = Vector2(240, 0)
			bubble.resized.connect(func(): _place_bubble(npc, bubble))
			bubble.mouse_filter = Control.MOUSE_FILTER_IGNORE
			var style := StyleBoxFlat.new()
			style.bg_color = Color(0.10, 0.14, 0.20, 0.97)
			style.content_margin_left = 7
			style.content_margin_right = 7
			style.content_margin_top = 5
			style.content_margin_bottom = 5
			style.corner_radius_top_left = 6
			style.corner_radius_top_right = 6
			style.corner_radius_bottom_left = 6
			style.corner_radius_bottom_right = 6
			bubble.add_theme_stylebox_override("panel", style)
			var text := Label.new()
			text.name = "Text"
			text.custom_minimum_size.x = 226
			text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			text.add_theme_font_size_override("font_size", 14)
			text.add_theme_color_override("font_color", Color.WHITE)
			bubble.add_child(text)
			bubble.z_index = 100
			npc.add_child(bubble)
			bubble_labels[npc.npc_name] = bubble
		bubble_labels[npc.npc_name].get_node("Text").text = str(line.text)
		bubble_labels[npc.npc_name].reset_size()
		_place_bubble(npc, bubble_labels[npc.npc_name])
		bubble_labels[npc.npc_name].show()

func _panel_done(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	panel_pending = false
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		_notice("面板读取失败，稍后重试")
		return
	var data = JSON.parse_string(body.get_string_from_utf8())
	if panel_name != picker.get_item_text(picker.selected):
		panel_timer = 0
		return
	if fetching == "relations" and data is Array:
		_draw_relations(data)
		var lines: PackedStringArray = []
		for edge in data:
			if edge.a != panel_name and edge.b != panel_name:
				continue
			lines.append("%s ↔ %s：%.0f / 100 · %s · %+.0f\n原因：%s\n" % [edge.a, edge.b, edge.score, edge.level, edge.delta, edge.reason])
		views[3].text = "\n".join(lines).replace("[", "[lb]")
	elif data is Dictionary:
		var keys := ["active", "forgotten", "reflections"]
		for i in range(3):
			var lines: PackedStringArray = []
			for item in data.get(keys[i], []):
				var strength: float = float(item.get("strength", 0))
				var bars := clampi(int(strength * 10), 0, 10)
				var memory_text := str(item.content).replace("[", "[lb]")
				if i == 1:
					memory_text = "[s]" + memory_text + "[/s]"
				lines.append("%s %s\n%s%s %.0f%% · 第%.0f分钟\n%s\n" % ["★" if item.type == "reflection" else "•", memory_text, "■".repeat(bars), "□".repeat(10-bars), strength * 100, item.game_time, item.get("forget_reason", "")])
			var content := "\n".join(lines) if not lines.is_empty() else "暂无记录"
			var color := "#a0a0a0" if i == 1 else ("#ffd978" if i == 2 else "#b5e5c2")
			views[i].text = "[color=" + color + "]" + content + "[/color]"

func _notice(message: String) -> void:
	notice = message
	notice_until = Time.get_ticks_msec() / 1000.0 + 5.0

# M3 修复：展示服务端的具体原因，区分选择错误、角色忙碌和连接失败。
func _send_control(path: String, payload: Dictionary) -> void:
	if not ApiClient.backend_online:
		_notice("后端未连接，请先启动后端")
		return
	if control_pending:
		_notice("上一项操作正在处理，请稍等")
		return
	control_pending = true
	var error := control_http.request(ApiClient.BACKEND + path, ["Content-Type: application/json"], HTTPClient.METHOD_POST, JSON.stringify(payload))
	if error != OK:
		control_pending = false
		_notice("请求未能发送，请稍后重试")

func _control_done(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	control_pending = false
	if result != HTTPRequest.RESULT_SUCCESS:
		_notice("连接超时或中断，请检查后端终端")
		return
	var data = JSON.parse_string(body.get_string_from_utf8())
	if code != 200:
		var detail := "请求失败（HTTP %d）" % code
		if data is Dictionary and data.get("detail") is String:
			detail = data.detail
		_notice(detail)
		return
	if data is Dictionary and data.has("paused"):
		paused = bool(data.paused)
		_notice("自动调度已暂停" if paused else "自动调度已继续")
	else:
		_notice(str(data.get("detail", "操作成功")) if data is Dictionary else "操作成功")
