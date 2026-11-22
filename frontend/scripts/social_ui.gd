extends CanvasLayer
## M3 独立观察界面：WebSocket 事件流、分组并发气泡、记忆/关系面板。
# NPC 对话和自言自语共享按事件排队的气泡。
const BUBBLE_SECONDS := 7.0
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
var relation_grid: GridContainer
var dialogue_log: RichTextLabel
var dialogue_by_actor := {}
var panel_http: HTTPRequest
var panel_timer := 0.0
var panel_pending := false
var picker: OptionButton
var tabs: TabContainer
var views: Array[RichTextLabel] = []
var panel: PanelContainer
var open_button: Button
var fetching := "memory"
var panel_name := ""
var bubble_labels := {}
# 每场对话独立显示，角色移动以世界状态为准。
var playbacks: Array = []
# M3 修复：保存正在生成的最新一句，重连后恢复，完成事件不重复播整场。
var live_versions := {}
# M3: 快照和断线补发可能包含同一句，以对话 ID/句序去重日志。
var logged_lines := {}
var notice := ""
var notice_until := 0.0
var profile_panel
var timeline_panel
var drag_handle: Label
var panel_drag := preload("res://scripts/window_drag.gd").new()

func setup(npcs: Array) -> void:
	actors = npcs

func _ready() -> void:
	layer = 10
	panel_http = HTTPRequest.new()
	panel_http.timeout = 5
	add_child(panel_http)
	panel_http.request_completed.connect(_panel_done)
	open_button = Button.new()
	open_button.text = "社交观察"
	add_child(open_button)
	panel = PanelContainer.new()
	panel.theme = preload("res://assets/tea_theme.tres")
	panel.position = Vector2(get_viewport().get_visible_rect().size.x - 612.0, 86)
	panel.size = Vector2(600, 558)
	panel.mouse_force_pass_scroll_events = false
	# 使用不透明背景，避免地图穿透影响阅读。
	var background := StyleBoxFlat.new()
	background.bg_color = Color("#f1ebe2")
	background.border_width_top = 3
	background.border_color = Color("#ac8666")
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
	open_button.pressed.connect(func(): panel.visible = not panel.visible)
	var box := VBoxContainer.new()
	panel.add_child(box)
	var title_row := HBoxContainer.new()
	box.add_child(title_row)
	drag_handle = Label.new()
	drag_handle.text = "社交观察"
	drag_handle.custom_minimum_size.y = 28
	drag_handle.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	drag_handle.add_theme_color_override("font_color", Color("#62442e"))
	title_row.add_child(drag_handle)
	var close := Button.new()
	close.text = "X"
	close.tooltip_text = "关闭"
	close.pressed.connect(panel.hide)
	title_row.add_child(close)
	add_child(panel_drag)
	panel_drag.bind(panel, drag_handle, true)
	picker = OptionButton.new()
	for npc in actors:
		picker.add_item(npc.npc_name)
	box.add_child(picker)
	picker.item_selected.connect(func(_i):
		panel_timer = 0
		var selected := picker.get_item_text(picker.selected)
		if profile_panel:
			profile_panel.select_npc(selected)
		timeline_panel.select_npc(selected)
		dialogue_log.text = dialogue_by_actor.get(selected, "暂无对话记录")
		for view in views:
			view.text = ""
		for page in tabs.get_children():
			if page is ScrollContainer:
				page.scroll_vertical = 0
	)
	tabs = TabContainer.new()
	tabs.custom_minimum_size = Vector2(480, 250)
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	tabs.add_theme_font_size_override("font_size", 15)
	box.add_child(tabs)
	for title in ["活跃记忆", "遗忘档案", "反思洞察", "关系"]:
		var scroll := ScrollContainer.new()
		scroll.name = title
		scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
		tabs.add_child(scroll)
		var view := RichTextLabel.new()
		view.bbcode_enabled = true
		view.fit_content = true
		view.scroll_active = false
		view.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		if title == "关系":
			var relation_box := VBoxContainer.new()
			relation_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			scroll.add_child(relation_box)
			relation_grid = GridContainer.new()
			relation_box.add_child(relation_grid)
			relation_box.add_child(view)
		else:
			scroll.add_child(view)
		views.append(view)
	dialogue_log = RichTextLabel.new()
	dialogue_log.name = "对话日志"
	dialogue_log.bbcode_enabled = false
	dialogue_log.text = "暂无对话记录"
	# M3: 新台词到达时跟随日志末尾，避免停在旧内容误以为未收到。
	dialogue_log.scroll_following = true
	tabs.add_child(dialogue_log)
	# 公开资料是独立页，沿用现有选择器；原记忆/关系/日志页的编号保持不变。
	profile_panel = load("res://scripts/persona_panel.gd").new()
	profile_panel.name = "角色资料"
	profile_panel.custom_minimum_size = Vector2(480, 250)
	profile_panel.setup(actors)
	tabs.add_child(profile_panel)
	timeline_panel = preload("res://scripts/event_timeline.gd").new()
	timeline_panel.name = "事件时间线"
	tabs.add_child(timeline_panel)
	if picker.item_count > 0:
		profile_panel.select_npc(picker.get_item_text(picker.selected))
		timeline_panel.select_npc(picker.get_item_text(picker.selected))
	tabs.tab_changed.connect(func(_index): panel_timer = 0)
	tabs.current_tab = 5


func show_profile(npc_name: String, toggle := false) -> bool:
	for index in picker.item_count:
		if picker.get_item_text(index) == npc_name:
			if (toggle and panel.visible and picker.selected == index
					and tabs.current_tab in [5, timeline_panel.get_index()]):
				panel.hide()
				return true
			panel.show()
			tabs.current_tab = 5
			picker.select(index)
			picker.item_selected.emit(index)
			panel_drag.clamp_position()
			return true
	return false


func _process(delta: float) -> void:
	_poll_socket(delta)
	_tick_playbacks(delta)
	panel_timer -= delta
	if not ApiClient.backend_online:
		return
	if panel.visible and panel_timer <= 0 and not panel_pending and picker.item_count > 0 and tabs.current_tab < 4:
		panel_timer = 2
		panel_pending = true
		panel_name = picker.get_item_text(picker.selected)
		fetching = "relations" if tabs.current_tab == 3 else "memory"
		var url := "/social/relations" if fetching == "relations" else "/npcs/" + panel_name.uri_encode() + "/memory"
		if panel_http.request(ApiClient.backend_url + url) != OK:
			panel_pending = false


func _tick_playbacks(delta: float) -> void:
	if ApiClient.simulation_paused:
		return
	for playback in playbacks.duplicate():
		if playback.waiting:
			var index := playbacks.find(playback)
			var actors_in_event: Array = playback.event.participants
			if playbacks.slice(0, index).any(func(p): return p.event.participants.any(func(n): return n in actors_in_event)):
				continue
			playback.waiting = false
			_advance_turn(playback)
			continue
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
			var url := ApiClient.backend_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws/events?after=" + str(cursor)
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
	if message.kind == "world_reset":
		cursor = 0
		ApiClient.apply_stream_world(message.world)
		return
	if message.kind == "heartbeat":
		return
	if message.kind == "snapshot":
		# M3: 后端重启可能重置事件编号，不能沿用旧对话的显示去重状态。
		if epoch != "" and epoch != str(message.epoch):
			logged_lines.clear()
			playbacks.clear()
			live_versions.clear()
			for bubble in bubble_labels.values():
				bubble.hide()
		epoch = str(message.epoch)
		# M3: 快照的 ID 可能是服务端最新值；不能提前跳过尚未收到的历史台词。
		cursor = mini(cursor, int(message.id))
		ApiClient.apply_stream_world(message.world)
		for line in message.get("live", []):
			_show_line(line)
			# M3: 日志交给随后按 ID 排序的事件补发，避免最新句插到旧句前面。
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
		"dialogue_finished":
			if message.get("replay", false):
				return
			for playback in playbacks.duplicate():
				if playback.event.get("conversation_id", "") == message.get("conversation_id", ""):
					playback.index = playback.event.result.transcript.size()
					_advance_turn(playback)
		"memory_changed", "relation_changed":
			panel_timer = 0
		"game_event_recorded":
			for actor: String in message.actors:
				timeline_panel.invalidate(actor)
		"error":
			if message.get("replay", false):
				return  # 旧执行错误不能取消当前恢复的气泡。
			for playback in playbacks.duplicate():
				if playback.event.get("conversation_id", "") == message.get("conversation_id", "") and not playback.event.get("ordered", false):
					playback.index = playback.event.result.transcript.size()
					_advance_turn(playback)
			_notice(str(message.message))
		# 对话日志保留完整台词；结束事件只收起地图上的气泡。

# M3: 事件补发只补日志；地图仍按服务端活跃快照和实时台词显示，避免重演旧聊天。
func _append_dialogue(line: Dictionary) -> void:
	var key := "%s:%s" % [line.conversation_id, int(line.sequence)]
	if logged_lines.has(key):
		return
	logged_lines[key] = true
	var entry := "%s：%s\n\n" % [line.speaker, _speech_text(line, str(line.text))]
	var participants: Array = line.get("participants", [line.speaker])
	for actor: String in participants:
		var history: String = dialogue_by_actor.get(actor, "") + entry
		dialogue_by_actor[actor] = history.right(12000) if history.length() > 16000 else history
	if picker.selected >= 0:
		var selected := picker.get_item_text(picker.selected)
		if selected in participants:
			dialogue_log.text = dialogue_by_actor[selected]

func _show_line(line: Dictionary) -> void:
	var participants: Array = line.get("participants", [])
	var speech: String = line.get("speech", "")
	if str(line.get("game_event_id", "")).is_empty() or not line.get("speaker", "") in participants:
		return
	if not ((speech == "npc_dialogue" and participants.size() == 2 and participants[0] != participants[1])
			or (speech == "event_self_talk" and participants.size() == 1)):
		return
	var key := str(line.conversation_id)
	if int(live_versions.get(key, -1)) >= int(line.sequence):
		return
	live_versions[key] = int(line.sequence)
	if speech == "npc_dialogue":
		for playback in playbacks.duplicate():
			if playback.event.get("speech") == "event_self_talk" and playback.event.participants[0] in participants:
				playback.index = playback.event.result.transcript.size()
				_advance_turn(playback)
	elif playbacks.any(func(p): return p.event.get("speech") == "npc_dialogue" and participants[0] in p.event.participants):
		return
	var display_lines := _display_lines(line)
	var ordered := bool(line.get("ordered", false))
	if ordered:
		for playback in playbacks:
			if playback.event.conversation_id == key:
				playback.event.result.transcript.append_array(display_lines)
				return
	else:
		for old in playbacks.duplicate():
			if not old.event.get("ordered", false) and old.event.participants.any(func(n): return n in line.participants):
				old.index = old.event.result.transcript.size()
				_advance_turn(old)
	_start_playback({"conversation_id": key, "participants": line.participants, "ordered": ordered, "speech": speech,
		"result": {"transcript": display_lines}}, float(line.get("playback_elapsed", 0.0)))

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
						style.bg_color = Color("#eed5ce").lerp(Color("#d7e5d6"), float(edge.score) / 100)
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
			segments.append({"text": text, "seconds": minf(BUBBLE_SECONDS, 4.0 + text.length() / 20.0)})
	for segment in segments:
		var text := _speech_text(line, str(segment.text))
		if not text.is_empty():
			lines.append({"speaker": line.speaker, "text": text, "seconds": float(segment.seconds)})
	return lines

func _speech_text(line: Dictionary, text: String) -> String:
	if line.get("speech") == "npc_dialogue":
		return text.replace("（自言自语）", "").replace("(自言自语)", "").strip_edges()
	return text


func _start_playback(event: Dictionary, elapsed: float = 0.0) -> void:
	var display := event.duplicate(true)
	var conflicts := playbacks.any(func(p): return p.event.participants.any(func(n): return n in event.participants))
	var playback := {"event": display, "index": 0, "remaining": 0.0, "waiting": conflicts}
	playbacks.append(playback)
	if conflicts:
		return
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
	if not playback.waiting:
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
			style.bg_color = Color("#f4e8d7")
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
			text.add_theme_color_override("font_color", Color("#37281e"))
			bubble.add_child(text)
			bubble.z_index = 100
			npc.add_child(bubble)
			bubble_labels[npc.npc_name] = bubble
		bubble_labels[npc.npc_name].get_node("Text").text = str(line.text)
		bubble_labels[npc.npc_name].reset_size()
		_place_bubble(npc, bubble_labels[npc.npc_name])
		bubble_labels[npc.npc_name].show()

func stop_actor_playback(actor_name: String) -> void:
	for playback in playbacks.duplicate():
		if actor_name in playback.event.participants:
			playback.index = playback.event.result.transcript.size()
			_advance_turn(playback)

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
		var fallback_time := float(ApiClient.last_world.get("minutes", ApiClient.display_minutes))
		var reference_time := float(data.get("as_of", fallback_time))
		var keys := ["active", "forgotten", "reflections"]
		for i in range(3):
			var lines: PackedStringArray = []
			for item in data.get(keys[i], []):
				var strength: float = float(item.get("strength", 0))
				var bars := clampi(int(strength * 10), 0, 10)
				var memory_text := str(item.content).replace("[", "[lb]")
				if item.type == "overheard":
					memory_text = "（旁听）" + memory_text
				if i == 1:
					memory_text = "[s]" + memory_text + "[/s]"
				var occurred_at := float(item.get("game_time", 0))
				var timing := "发生于：%s\n距今：%s" % [_format_game_timestamp(occurred_at), _format_game_duration(reference_time - occurred_at)]
				if i == 1 and float(item.get("forgotten_at", 0)) > 0:
					timing += "\n遗忘于：" + _format_game_timestamp(float(item.forgotten_at))
				lines.append("%s %s\n%s%s %.0f%%\n%s\n%s\n" % ["★" if item.type == "reflection" else "•", memory_text, "■".repeat(bars), "□".repeat(10-bars), strength * 100, timing, item.get("forget_reason", "")])
			var content := "\n".join(lines) if not lines.is_empty() else "暂无记录"
			var color := "#6a6259" if i == 1 else ("#805429" if i == 2 else "#395b47")
			views[i].text = "[color=" + color + "]" + content + "[/color]"

func _notice(message: String) -> void:
	notice = message
	notice_until = Time.get_ticks_msec() / 1000.0 + 5.0
