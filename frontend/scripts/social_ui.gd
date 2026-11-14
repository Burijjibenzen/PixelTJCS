extends CanvasLayer
## M3 独立观察界面：WebSocket 事件流、分组并发气泡、记忆/关系面板。
# 原版节奏：新台词立即替换，每段最多显示十个现实秒；不限制人物行动。
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
# 每场对话独立显示，显示计时不参与人物行为或角色占用。
var playbacks: Array = []
# M3 修复：保存正在生成的最新一句，重连后恢复，完成事件不重复播整场。
var live_versions := {}
# M3: 快照和断线补发可能包含同一句，以对话 ID/句序去重日志。
var logged_lines := {}
var meeting_status: Label
var notice := ""
var notice_until := 0.0
var speed_picker: OptionButton
var profile_panel
var profile_hidden: Array[Control] = []
var _memory_history_view := false
var _memory_reference_minutes := 0.0
var drag_handle: Label
var _dragging := false
var _drag_offset := Vector2.ZERO

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
	# 使用不透明背景，避免地图穿透影响阅读。
	var background := StyleBoxFlat.new()
	background.bg_color = Color(0.08, 0.10, 0.14, 1.0)
	background.content_margin_left = 12
	background.content_margin_right = 12
	background.content_margin_top = 12
	background.content_margin_bottom = 12
	background.corner_radius_top_left = 8
	background.corner_radius_top_right = 8
	background.corner_radius_bottom_left = 8
	background.corner_radius_bottom_right = 8
	panel.add_theme_stylebox_override("panel", background)
	add_child(panel)
	open.pressed.connect(func(): panel.visible = not panel.visible)
	var box := VBoxContainer.new()
	panel.add_child(box)
	var title_row := HBoxContainer.new()
	box.add_child(title_row)
	drag_handle = Label.new()
	drag_handle.text = "社交观察　·　拖动此处移动"
	drag_handle.custom_minimum_size.y = 28
	drag_handle.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	drag_handle.add_theme_color_override("font_color", Color(0.70, 0.83, 0.90))
	drag_handle.mouse_filter = Control.MOUSE_FILTER_STOP
	drag_handle.mouse_default_cursor_shape = Control.CURSOR_MOVE
	drag_handle.tooltip_text = "按住鼠标左键拖动标题栏，调整面板位置"
	drag_handle.gui_input.connect(_drag_handle_input)
	title_row.add_child(drag_handle)
	var collapse := Button.new()
	collapse.text = "收起"
	collapse.pressed.connect(func():
		_dragging = false
		panel.hide()
	)
	title_row.add_child(collapse)
	panel.resized.connect(_clamp_panel_position)
	get_viewport().size_changed.connect(_clamp_panel_position)
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
	pause.text = "暂停 / 继续世界"
	controls.add_child(pause)
	pause.pressed.connect(func():
		var desired := not bool(ApiClient.last_world.get("social", {}).get("paused", false))
		_send_control("/social/control", {"paused": desired})
	)
	speed_picker = OptionButton.new()
	for rate in ApiClient.SPEED_OPTIONS:
		speed_picker.add_item("正常 ×1" if rate == ApiClient.BASE_SPEED else "快进 ×%.0f" % (rate / ApiClient.BASE_SPEED))
	speed_picker.tooltip_text = "原速：现实1秒 = 游戏10分钟；快进同步加速移动，对话恢复原版节奏"
	controls.add_child(speed_picker)
	speed_picker.item_selected.connect(func(index): ApiClient.set_speed(ApiClient.SPEED_OPTIONS[index]))
	var advance := Button.new()
	advance.text = "快进3天"
	advance.tooltip_text = "连续快进3个游戏天，抵达目标后恢复原倍率"
	controls.add_child(advance)
	advance.pressed.connect(func(): ApiClient.advance_time(4320))
	ApiClient.speed_changed.connect(_sync_speed_picker)
	ApiClient.control_error.connect(_notice)
	_sync_speed_picker()
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
		if profile_panel:
			profile_panel.select_npc(picker.get_item_text(picker.selected))
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
	# 公开资料是独立页，沿用现有选择器；原记忆/关系/日志页的编号保持不变。
	profile_panel = load("res://scripts/persona_panel.gd").new()
	profile_panel.name = "角色资料"
	profile_panel.custom_minimum_size = Vector2(480, 250)
	profile_panel.setup(actors)
	tabs.add_child(profile_panel)
	if picker.item_count > 0:
		profile_panel.select_npc(picker.get_item_text(picker.selected))
	profile_hidden = [status, model_status, meet_row, cancel, meeting_status, history_label, history_slider]
	tabs.tab_changed.connect(_on_tab_changed)
	tabs.current_tab = 5
	_on_tab_changed(tabs.current_tab)
	_clamp_panel_position.call_deferred()


func _drag_handle_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
		_dragging = true
		_drag_offset = event.global_position - panel.position
		drag_handle.accept_event()


func _input(event: InputEvent) -> void:
	if not _dragging:
		return
	if not panel.visible:
		_dragging = false
		return
	if event is InputEventMouseMotion:
		if not event.button_mask & MOUSE_BUTTON_MASK_LEFT:
			_dragging = false
			return
		panel.position = event.position - _drag_offset
		_clamp_panel_position()
		get_viewport().set_input_as_handled()
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if not event.pressed:
			_dragging = false
		get_viewport().set_input_as_handled()


func _clamp_panel_position() -> void:
	if not is_instance_valid(panel):
		return
	var bounds := (get_viewport().get_visible_rect().size - panel.size).max(Vector2.ZERO)
	panel.position = panel.position.clamp(Vector2.ZERO, bounds)


func show_profile(npc_name: String) -> bool:
	for index in picker.item_count:
		if picker.get_item_text(index) == npc_name:
			panel.show()
			tabs.current_tab = 5
			picker.select(index)
			picker.item_selected.emit(index)
			_clamp_panel_position()
			return true
	return false


func _on_tab_changed(index: int) -> void:
	panel_timer = 0
	# 资料页专注人物信息；原有会面和记忆时间线控件在其它页保持可用。
	for control in profile_hidden:
		control.visible = index != 5
	tabs.custom_minimum_size.y = 450 if index == 5 else 250
	panel.reset_size()

func _process(delta: float) -> void:
	_poll_socket(delta)
	_tick_playbacks(delta)
	var meeting_lines: PackedStringArray = []
	var selected := picker.get_item_text(picker.selected)
	for m in ApiClient.last_world.get("social", {}).get("meetings", []):
		if selected in [m.a, m.b]:
			meeting_lines.append("%s ↔ %s：%s" % [m.a, m.b, m.detail])
	meeting_status.text = "\n".join(meeting_lines.slice(maxi(0, meeting_lines.size() - 3)))
	model_status.text = "模型已配置" if ApiClient.last_world.get("social", {}).get("model_configured", false) else "预设台词模式：未配置模型密钥"
	panel_timer -= delta
	status.text = notice if Time.get_ticks_msec() / 1000.0 < notice_until else ("世界已暂停 · " if ApiClient.simulation_paused else "自主社交 · ") + ApiClient.backend_time
	var target = ApiClient.last_world.get("social", {}).get("target_minutes")
	if target != null and Time.get_ticks_msec() / 1000.0 >= notice_until:
		var remaining := maxf(0.0, float(target) - float(ApiClient.last_world.get("minutes", 0.0)))
		status.text += "\n定时快进中：剩余%s（游戏时间），到达后恢复原速" % _format_game_duration(ceilf(remaining))
	if not stream_online:
		status.text = "对话事件流重连中 · " + ApiClient.backend_time
	if not ApiClient.backend_online:
		status.text = "WebSocket 重连中 · 世界接口暂未连接"
		return
	if panel.visible and panel_timer <= 0 and not panel_pending and picker.item_count > 0 and tabs.current_tab < 4:
		panel_timer = 2
		panel_pending = true
		panel_name = picker.get_item_text(picker.selected)
		fetching = "relations" if tabs.current_tab == 3 else "memory"
		var url := "/social/relations" if fetching == "relations" else "/npcs/" + panel_name.uri_encode() + "/memory"
		if fetching == "memory":
			var index := int(history_slider.value)
			_memory_history_view = index < history_items.size()
			if index < history_items.size():
				url += "?snapshot_id=" + str(int(history_items[index].id))
				_memory_reference_minutes = float(history_items[index].game_time)
				history_label.text = "历史快照：" + _format_game_timestamp(_memory_reference_minutes)
			else:
				history_label.text = "记忆时间线：当前"
		if panel_http.request(ApiClient.BACKEND + url) != OK:
			panel_pending = false


func _sync_speed_picker() -> void:
	var index := ApiClient.SPEED_OPTIONS.find(ApiClient.simulation_speed)
	if index >= 0:
		speed_picker.select(index)


func _tick_playbacks(delta: float) -> void:
	for playback in playbacks.duplicate():
		playback.remaining -= delta
		while playback in playbacks and playback.remaining <= 0:
			var overdue: float = -playback.remaining
			_advance_turn(playback)
			playback.remaining -= overdue

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
		"error":
			if message.get("replay", false):
				return  # 旧执行错误不能取消当前恢复的气泡。
			for playback in playbacks.duplicate():
				if playback.event.get("conversation_id", "") == message.get("conversation_id", ""):
					playback.index = playback.event.result.transcript.size()
					_advance_turn(playback)
			_notice(str(message.message))
		# 角色任务完成与气泡显示分别处理，不因气泡而延迟后续行动。

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
	var display_lines := _display_lines(line)
	# 恢复原版即时替换，不让文字展示反过来影响人物行为。
	for old in playbacks.duplicate():
		if old.event.participants.any(func(n): return n in line.participants):
			for npc in actors:
				if npc.npc_name in old.event.participants:
					if bubble_labels.has(npc.npc_name):
						bubble_labels[npc.npc_name].hide()
			playbacks.erase(old)
	_start_playback({"conversation_id": key, "participants": line.participants,
		"result": {"transcript": display_lines}}, float(line.get("playback_elapsed", 0.0)))

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

func _display_lines(line: Dictionary) -> Array:
	var lines: Array = []
	var segments: Array = line.get("segments", []).duplicate(true)
	if segments.is_empty():
		for text in _segments(str(line.text)):
			segments.append({"text": text, "seconds": BUBBLE_SECONDS})
	for segment in segments:
		lines.append({"speaker": line.speaker, "text": segment.text, "seconds": float(segment.seconds)})
	return lines


func _start_playback(event: Dictionary, elapsed: float = 0.0) -> void:
	var display := event.duplicate(true)
	var playback := {"event": display, "index": 0, "remaining": 0.0}
	playbacks.append(playback)
	_advance_turn(playback)
	# 重连时从当前段恢复，并保留该句后续段落及尚未阅读的台词。
	while playback in playbacks and elapsed > 0:
		if elapsed < playback.remaining:
			playback.remaining -= elapsed
			break
		elapsed -= playback.remaining
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
		playbacks.erase(playback)
		panel_timer = 0
		return
	var line: Dictionary = event.result.transcript[playback.index]
	playback.index += 1
	playback.remaining = float(line.get("seconds", BUBBLE_SECONDS))
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

func _format_game_duration(minutes: float) -> String:
	var total_minutes := maxi(0, int(floor(minutes)))
	var parts: PackedStringArray = []
	var days := int(total_minutes / 1440)
	var hours := int((total_minutes % 1440) / 60)
	var remaining := total_minutes % 60
	if days > 0:
		parts.append("%d 天" % days)
	if hours > 0:
		parts.append("%d 小时" % hours)
	if remaining > 0 or parts.is_empty():
		parts.append("%d 分钟" % remaining)
	return " ".join(parts)


func _format_game_timestamp(minutes: float) -> String:
	var total_minutes := maxi(0, int(floor(minutes)))
	return "第 %d 天 %02d:%02d" % [int(total_minutes / 1440) + 1, int((total_minutes % 1440) / 60), total_minutes % 60]


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
		# 历史强度和时间都以该快照为准，不能将过去状态误写成当前状态。
		var fallback_time := _memory_reference_minutes if _memory_history_view else float(ApiClient.last_world.get("minutes", ApiClient.display_minutes))
		var reference_time := float(data.get("as_of", fallback_time))
		var keys := ["active", "forgotten", "reflections"]
		for i in range(3):
			var lines: PackedStringArray = []
			for item in data.get(keys[i], []):
				var strength: float = float(item.get("strength", 0))
				var bars := clampi(int(strength * 10), 0, 10)
				var memory_text := str(item.content).replace("[", "[lb]")
				if i == 1:
					memory_text = "[s]" + memory_text + "[/s]"
				var occurred_at := float(item.get("game_time", 0))
				var timing := "发生于：%s\n%s：%s" % [_format_game_timestamp(occurred_at), "距当时" if _memory_history_view else "距今", _format_game_duration(reference_time - occurred_at)]
				if i == 1 and float(item.get("forgotten_at", 0)) > 0:
					timing += "\n遗忘于：" + _format_game_timestamp(float(item.forgotten_at))
				lines.append("%s %s\n%s%s %.0f%%\n%s\n%s\n" % ["★" if item.type == "reflection" else "•", memory_text, "■".repeat(bars), "□".repeat(10-bars), strength * 100, timing, item.get("forget_reason", "")])
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
		_notice("世界已暂停" if paused else "世界已继续")
	else:
		_notice(str(data.get("detail", "操作成功")) if data is Dictionary else "操作成功")
