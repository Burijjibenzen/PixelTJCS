extends CanvasLayer
signal stage_selection_changed(enabled: bool)
signal reset_started
## 公告浏览/发布是观察者操作，不代替NPC实际阅读。
var actors: Array = []
var boards: Array = []
var board_id := ""
var backend_url := ""
var cache := {}
var pending := false
var request_board := ""
var operation := ""
var request_draft := {}
var refresh_timer := 0.0
var http: HTTPRequest
var picker: OptionButton
var publish_button: Button
var read_status: Label
var content_edit: TextEdit
var status: Label
var cards: VBoxContainer
var cards_scroll: ScrollContainer
var heading_title: Label
var admin_controls: VBoxContainer
var tabs: TabContainer
var whisper_cards: VBoxContainer
var world_controls: HBoxContainer
var delete_confirm: ConfirmationDialog
var source_picker: OptionButton
var whisper_edit: TextEdit
var whisper_button: Button
var reset_button: Button
var reset_confirm: ConfirmationDialog
var reset_overlay: CanvasLayer
var god_mode := false
var stage_button: Button
var stage_status: Label
var stage_http: HTTPRequest
var stage_selecting := false
var stage_pending := false
var stage_target := ""
var stage_appearance := false

func setup(npcs: Array) -> void:
	actors = npcs

func _ready() -> void:
	layer = 20
	process_mode = Node.PROCESS_MODE_ALWAYS
	if backend_url.is_empty():
		backend_url = ApiClient.backend_url
	http = HTTPRequest.new()
	http.timeout = 4
	add_child(http)
	http.request_completed.connect(_done)
	stage_http = HTTPRequest.new()
	stage_http.timeout = 8
	add_child(stage_http)
	stage_http.request_completed.connect(_stage_done)
	var panel := PanelContainer.new()
	panel.name = "Window"
	panel.theme = preload("res://assets/tea_theme.tres")
	panel.position = Vector2(280, 86)
	panel.size = Vector2(720, 570)
	panel.mouse_force_pass_scroll_events = false
	panel.minimum_size_changed.connect(func(): panel.size = Vector2(720, 570))
	var surface := StyleBoxFlat.new()
	surface.bg_color = Color("#f3e9dc")
	surface.set_corner_radius_all(8)
	surface.set_content_margin_all(14)
	panel.add_theme_stylebox_override("panel", surface)
	add_child(panel)
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 8)
	panel.add_child(body)
	var heading := HBoxContainer.new()
	body.add_child(heading)
	heading_title = _label("校园公告栏", 21)
	heading_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(heading_title)
	var close := Button.new()
	close.text = "X"
	close.tooltip_text = "关闭"
	close.pressed.connect(hide)
	heading.add_child(close)
	var drag := preload("res://scripts/window_drag.gd").new()
	add_child(drag)
	drag.bind(panel, heading_title)
	status = _label("", 13)
	status.custom_minimum_size.y = 22
	body.add_child(status)
	tabs = TabContainer.new()
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(tabs)
	var notices_page := VBoxContainer.new()
	notices_page.name = "公告"
	tabs.add_child(notices_page)
	notices_page.add_child(_label("角色会从同一走廊走到公告栏前，再停下阅读。", 13))
	picker = OptionButton.new()
	notices_page.add_child(picker)
	for map_id in Game.MAP_IDS:
		for board: Dictionary in Game.floor_data[map_id].get("noticeboards", []):
			boards.append(board)
			picker.add_item(str(board.location) + " · 公告栏")
	picker.item_selected.connect(func(index): open_board(str(boards[index].id), god_mode))
	notices_page.add_child(_label("历史公告", 16))
	cards_scroll = ScrollContainer.new()
	cards_scroll.custom_minimum_size.y = 145
	cards_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	cards_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	notices_page.add_child(cards_scroll)
	cards = VBoxContainer.new()
	cards.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cards.add_theme_constant_override("separation", 10)
	cards_scroll.add_child(cards)
	admin_controls = VBoxContainer.new()
	admin_controls.hide()
	notices_page.add_child(admin_controls)
	content_edit = TextEdit.new()
	content_edit.placeholder_text = "公告正文（最多500字）"
	content_edit.custom_minimum_size.y = 76
	content_edit.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	admin_controls.add_child(content_edit)
	content_edit.text_changed.connect(_update_buttons)
	publish_button = Button.new()
	publish_button.text = "发布到此公告栏"
	publish_button.pressed.connect(publish)
	admin_controls.add_child(publish_button)
	read_status = _label("角色靠近后会自动阅读，无需手动安排。", 13)
	notices_page.add_child(read_status)
	var whisper_page := VBoxContainer.new()
	whisper_page.name = "悄悄话"
	tabs.add_child(whisper_page)
	whisper_page.add_child(_label("悄悄话历史", 16))
	var history_scroll := ScrollContainer.new()
	history_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	history_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	whisper_page.add_child(history_scroll)
	whisper_cards = VBoxContainer.new()
	whisper_cards.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	history_scroll.add_child(whisper_cards)
	whisper_edit = TextEdit.new()
	whisper_edit.placeholder_text = "例如：去图书分馆查阅资料，停留半小时"
	whisper_edit.custom_minimum_size.y = 54
	whisper_edit.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	whisper_edit.text_changed.connect(_update_buttons)
	whisper_page.add_child(whisper_edit)
	var whisper_controls := HBoxContainer.new()
	whisper_page.add_child(whisper_controls)
	source_picker = OptionButton.new()
	for npc in actors:
		source_picker.add_item(npc.npc_name)
	source_picker.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	whisper_controls.add_child(source_picker)
	whisper_button = Button.new()
	whisper_button.text = "发送悄悄话"
	whisper_button.pressed.connect(_whisper)
	whisper_controls.add_child(whisper_button)
	_render_whispers([])
	world_controls = HBoxContainer.new()
	body.add_child(world_controls)
	stage_button = Button.new()
	stage_button.pressed.connect(func(): _set_stage_selection(not stage_selecting))
	world_controls.add_child(stage_button)
	stage_status = _label("", 13)
	stage_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	world_controls.add_child(stage_status)
	reset_button = Button.new()
	reset_button.text = "重置世界"
	reset_button.pressed.connect(_open_reset_confirmation)
	world_controls.add_child(reset_button)
	_setup_reset_confirmation(panel.theme)
	delete_confirm = ConfirmationDialog.new()
	delete_confirm.theme = panel.theme
	delete_confirm.title = "删除公告"
	delete_confirm.dialog_text = "确定删除这条公告？角色已经获得的记忆会保留。"
	delete_confirm.ok_button_text = "删除"
	delete_confirm.confirmed.connect(func():
		if board_id == delete_confirm.get_meta("board_id"):
			_request("delete", {"notice_id": delete_confirm.get_meta("notice_id")}))
	add_child(delete_confirm)
	tabs.tab_changed.connect(func(_index):
		if visible and not board_id.is_empty():
			status.text = ""
			refresh())
	_set_stage_selection(false)
	visibility_changed.connect(func():
		if not visible:
			_set_stage_selection(false))
	hide()

func _setup_reset_confirmation(base_theme: Theme) -> void:
	var dialog_theme := base_theme.duplicate() as Theme
	var frame := StyleBoxFlat.new()
	frame.bg_color = Color("#3f5540")
	frame.set_expand_margin(SIDE_TOP, 42)
	frame.set_corner_radius_all(9)
	frame.shadow_color = Color(0.1, 0.075, 0.05, 0.45)
	frame.shadow_size = 12
	frame.shadow_offset = Vector2(0, 5)
	dialog_theme.set_stylebox("embedded_border", "Window", frame)
	dialog_theme.set_stylebox("embedded_unfocused_border", "Window", frame)
	dialog_theme.set_color("title_color", "Window", Color("#fff6df"))
	dialog_theme.set_font_size("title_font_size", "Window", 22)
	dialog_theme.set_constant("title_height", "Window", 42)
	dialog_theme.set_constant("close_h_offset", "Window", 18)
	dialog_theme.set_constant("close_v_offset", "Window", 24)
	dialog_theme.set_constant("buttons_separation", "AcceptDialog", 12)

	var paper := StyleBoxFlat.new()
	paper.bg_color = Color("#fff6e4")
	paper.set_content_margin_all(24)
	paper.corner_radius_bottom_left = 9
	paper.corner_radius_bottom_right = 9
	dialog_theme.set_stylebox("panel", "AcceptDialog", paper)

	reset_confirm = ConfirmationDialog.new()
	reset_confirm.theme = dialog_theme
	reset_confirm.title = "重置世界"
	reset_confirm.dialog_autowrap = true
	reset_confirm.dialog_text = "时钟回到第 1 天 08:00，所有角色回到初始位置。\n\n将清空玩家存档、记忆、事件、公告、关系变化和临时行程。\n\n重置后无法恢复。"
	reset_confirm.ok_button_text = "确认重置"
	reset_confirm.cancel_button_text = "取消"
	reset_confirm.unresizable = true
	reset_confirm.confirmed.connect(_reset_world)
	add_child(reset_confirm)

	var details := reset_confirm.get_label()
	details.add_theme_font_size_override("font_size", 19)
	details.add_theme_color_override("font_color", Color("#35291f"))
	details.add_theme_constant_override("line_spacing", 6)
	var cancel := reset_confirm.get_cancel_button()
	var confirm := reset_confirm.get_ok_button()
	for button: Button in [cancel, confirm]:
		button.custom_minimum_size = Vector2(110, 44)
		button.add_theme_font_size_override("font_size", 18)
	for state: String in ["normal", "hover", "pressed"]:
		var danger := confirm.get_theme_stylebox(state).duplicate() as StyleBoxFlat
		danger.bg_color = {"normal": Color("#963f36"), "hover": Color("#81332b"), "pressed": Color("#6e2a24")}[state]
		danger.border_color = Color("#763029")
		danger.set_corner_radius_all(5)
		confirm.add_theme_stylebox_override(state, danger)
	for item: String in ["font_color", "font_hover_color", "font_pressed_color", "font_focus_color"]:
		confirm.add_theme_color_override(item, Color("#fff8ed"))

func _open_reset_confirmation() -> void:
	var width := mini(540, int(get_viewport().get_visible_rect().size.x) - 48)
	reset_confirm.min_size = Vector2i(maxi(1, width), 0)
	reset_confirm.popup_centered(Vector2i(reset_confirm.min_size.x, 280))
	reset_confirm.get_cancel_button().grab_focus.call_deferred()

func _label(text: String, font_size: int) -> Label:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", font_size)
	return label

func open_board(key := "", admin := false) -> void:
	if boards.is_empty():
		return
	if key.is_empty():
		key = str(boards[0].id)
	var found := -1
	for index in boards.size():
		if boards[index].id == key:
			found = index
			break
	if found < 0:
		return
	board_id = key
	god_mode = admin
	if not god_mode:
		_set_stage_selection(false)
	heading_title.text = "上帝模式" if god_mode else "校园公告栏"
	admin_controls.visible = god_mode
	world_controls.visible = god_mode
	read_status.visible = not god_mode
	tabs.tabs_visible = god_mode
	tabs.set_tab_hidden(1, not god_mode)
	picker.select(found)
	show()
	_render(cache.get(key, {}).get("notices", []))
	status.text = "后端未连接" if not ApiClient.backend_online else "正在读取公告…"
	refresh_timer = 3.0
	_update_buttons()
	refresh()

func _process(delta: float) -> void:
	if not visible or is_resetting():
		return
	_update_buttons()
	if not ApiClient.backend_online:
		status.text = "后端未连接，当前显示缓存公告。" if cache.has(board_id) else "后端未连接，连接后可查看与发布公告。"
		return
	# 输入焦点（包括中文输入法正在组词）或未发布草稿存在时，不自动请求。
	if god_mode and (content_edit.has_focus() or whisper_edit.has_focus() or reset_confirm.visible
			or delete_confirm.visible or not content_edit.text.is_empty() or not whisper_edit.text.is_empty()):
		refresh_timer = 3.0
		return
	refresh_timer -= delta
	if refresh_timer <= 0:
		refresh_timer = 3.0
		refresh()

func refresh() -> void:
	_request("history" if god_mode and tabs.current_tab == 1 else "load")

func publish() -> void:
	if god_mode:
		_request("publish", {"content": content_edit.text.strip_edges()})

func _whisper() -> void:
	_request("whisper", {"npc": source_picker.get_item_text(source_picker.selected),
		"text": whisper_edit.text.strip_edges()})

func _set_stage_selection(enabled: bool) -> void:
	stage_selecting = enabled
	stage_button.text = "取消角色选择" if enabled else "退场／出演"
	stage_status.text = "在左侧选择一名角色" if enabled else ""
	stage_selection_changed.emit(enabled)
	_update_buttons()

func set_actor_stage(npc_name: String, on_stage: bool) -> void:
	var world: String = ApiClient.last_world.get("social", {}).get("world_id", "")
	if not stage_selecting or stage_pending or not ApiClient.backend_online or world.is_empty():
		return
	stage_pending = true
	stage_target = npc_name
	stage_appearance = on_stage
	stage_status.text = "正在更新%s…" % npc_name
	var payload := JSON.stringify({"npc": npc_name, "on_stage": on_stage, "world_id": world})
	if stage_http.request(backend_url + "/world/cast", ApiClient.world_headers(), HTTPClient.METHOD_POST, payload) != OK:
		stage_pending = false
		stage_status.text = "角色状态请求发送失败，请重试"
	_update_buttons()

func _stage_done(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	stage_pending = false
	var data = null if body.is_empty() else JSON.parse_string(body.get_string_from_utf8())
	if result == HTTPRequest.RESULT_SUCCESS and code == 200 and data is Dictionary:
		ApiClient.apply_stream_world(data.world)
		_set_stage_selection(false)
		stage_status.text = "%s已%s" % [stage_target, "出演" if stage_appearance else "退场"]
	else:
		stage_status.text = str(data.get("detail", "角色状态更新失败，请重试")) if data is Dictionary else "连接失败，请重试"
	_update_buttons()

func _reset_world() -> void:
	var world_id: String = ApiClient.last_world.get("social", {}).get("world_id", "")
	if not world_id.is_empty():
		_request("reset", {"confirm": true, "world_id": world_id})

func is_resetting() -> bool:
	return is_instance_valid(reset_overlay)

func _show_reset_overlay() -> void:
	reset_overlay = preload("res://scripts/world_reset_overlay.gd").new()
	get_parent().add_child(reset_overlay)
	reset_started.emit()

func _request(kind: String, payload := {}) -> void:
	if board_id.is_empty() or not ApiClient.backend_online:
		return
	if pending:
		if operation not in ["load", "history"] or kind == operation:
			return
		http.cancel_request()
	pending = true
	operation = kind
	request_board = board_id
	request_draft = {"content": content_edit.text} if kind == "publish" else {}
	if kind == "whisper":
		request_draft = {"text": whisper_edit.text}
		status.text = "正在解析悄悄话…"
	if kind == "reset":
		status.text = "正在重置世界…"
	# 重置要等正在执行的后台工作退出，仍可通过事件流收到完成状态。
	http.timeout = 0.0 if kind == "reset" else 32.0 if kind == "whisper" else 4.0
	_update_buttons()
	var path := "/noticeboards/" + board_id.uri_encode() + ("/read" if kind == "read" else "/notices")
	if kind in ["whisper", "reset"]:
		path = "/world/" + kind
	if kind == "history":
		path = "/world/whisper/history"
	if kind == "delete":
		path += "/" + str(payload.notice_id).uri_encode()
	var method := HTTPClient.METHOD_GET if kind in ["load", "history"] else HTTPClient.METHOD_POST
	if kind == "delete":
		method = HTTPClient.METHOD_DELETE
	if http.request(backend_url + path, ApiClient.world_headers(), method, JSON.stringify(payload) if method == HTTPClient.METHOD_POST else "") != OK:
		pending = false
		status.text = "请求未能发送，请检查后端连接。"
		_update_buttons()
	elif kind == "reset":
		_show_reset_overlay()

func _done(result: int, code: int, _headers: PackedStringArray, bytes: PackedByteArray) -> void:
	var data = null if bytes.is_empty() else JSON.parse_string(bytes.get_string_from_utf8())
	var completed_board := request_board
	var completed_operation := operation
	var completed_draft := request_draft
	pending = false
	_update_buttons()
	if completed_operation == "reset":
		_finish_reset(result, code, data)
		return
	if result != HTTPRequest.RESULT_SUCCESS or code != 200 or not data is Dictionary:
		if board_id == completed_board:
			status.text = str(data.get("detail", "操作失败，请重试。")) if data is Dictionary else "连接失败，请检查后端。"
	elif completed_operation == "whisper":
		if god_mode:
			status.text = "已指派 " + str(data.activity.title)
			if whisper_edit.text == completed_draft.get("text", ""):
				whisper_edit.clear()
			refresh()
	elif completed_operation == "history":
		_render_whispers(data.get("history", []))
		if god_mode and tabs.current_tab == 1 and not status.text.begins_with("已指派"):
			status.text = "共 %d 条已发送指令" % data.get("history", []).size()
	elif completed_operation in ["load", "delete"]:
		var changed: bool = cache.get(completed_board, {}).get("notices", []) != data.get("notices", [])
		cache[completed_board] = data
		if board_id == completed_board:
			if changed:
				_render(data.get("notices", []))
			status.text = "已删除公告" if completed_operation == "delete" else "共 %d 条公告" % data.get("notices", []).size()
			var readers: PackedStringArray = []
			for row: Dictionary in data.get("readers", []):
				readers.append(str(row.npc) + "：" + str(row.detail))
			read_status.text = "　".join(readers) if not readers.is_empty() else "角色靠近后会自动阅读，无需手动安排。"
	else:
		if board_id == completed_board:
			status.text = "发布成功" if completed_operation == "publish" else str(data.get("detail", "角色已开始阅读"))
			# 只清空刚刚提交的版本，不能删除请求期间继续输入的新内容。
			if completed_operation == "publish" and content_edit.text == completed_draft.get("content", ""):
				content_edit.clear()
		refresh_timer = 0.5
	if board_id != completed_board:
		refresh()

func _finish_reset(result: int, code: int, data: Variant) -> void:
	var world = data.get("world") if data is Dictionary else null
	var social = world.get("social") if world is Dictionary else null
	if (result == HTTPRequest.RESULT_SUCCESS and code == 200 and world is Dictionary
			and world.get("npcs") is Dictionary and social is Dictionary and not str(social.get("world_id", "")).is_empty()):
		var old_world := ApiClient.world_id
		ApiClient.apply_stream_world(world)
		hide()
		# 场景切换时由旧场景销毁遮罩，避免重载前短暂放开输入。
		if not old_world.is_empty() and ApiClient.world_id != old_world and get_tree().current_scene != null:
			return
	else:
		status.text = str(data.get("detail", "重置失败，请重试。")) if data is Dictionary else "连接失败，请检查后端。"
	if is_resetting():
		reset_overlay.queue_free()

func _render(notices: Array) -> void:
	for child in cards.get_children():
		cards.remove_child(child)
		child.queue_free()
	if notices.is_empty():
		cards.add_child(_label("暂无公告。", 15))
	for notice: Dictionary in notices:
		var card := VBoxContainer.new()
		card.add_theme_constant_override("separation", 6)
		cards.add_child(card)
		var header := HBoxContainer.new()
		card.add_child(header)
		var stamp := _label("发布于：" + _timestamp(float(notice.get("published_at", 0))), 12)
		stamp.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		header.add_child(stamp)
		if god_mode:
			var remove := Button.new()
			remove.text = "删除"
			remove.pressed.connect(func():
				delete_confirm.set_meta("board_id", board_id)
				delete_confirm.set_meta("notice_id", notice.id)
				delete_confirm.popup_centered())
			header.add_child(remove)
		card.add_child(_label(str(notice.get("content", "")), 15))
		card.add_child(HSeparator.new())

func _render_whispers(history: Array) -> void:
	if whisper_cards.has_meta("history") and whisper_cards.get_meta("history") == history:
		return
	whisper_cards.set_meta("history", history)
	for child in whisper_cards.get_children():
		whisper_cards.remove_child(child)
		child.queue_free()
	if history.is_empty():
		whisper_cards.add_child(_label("暂无悄悄话。", 15))
	for row: Dictionary in history:
		whisper_cards.add_child(_label("%s · %s · %s" % [_timestamp(float(row.game_time)), row.npc, row.action], 13))
		whisper_cards.add_child(_label(str(row.text), 15))
		whisper_cards.add_child(HSeparator.new())

func _timestamp(at: float) -> String:
	var minutes := maxi(0, int(at))
	return "第 %d 天 %02d:%02d" % [int(minutes / 1440) + 1, int((minutes % 1440) / 60), minutes % 60]

func _update_buttons() -> void:
	var connected := ApiClient.backend_online and not board_id.is_empty()
	var available := connected and (not pending or operation in ["load", "history"])
	stage_button.disabled = not connected or stage_pending or (pending and operation == "reset") or ApiClient.last_world.get("social", {}).get("world_id", "") == ""
	var whisper := whisper_edit.text.strip_edges()
	whisper_button.disabled = not available or source_picker.item_count == 0 or whisper.is_empty() or whisper.length() > 500
	reset_button.disabled = not available or stage_pending or ApiClient.last_world.get("social", {}).get("world_id", "") == ""
	publish_button.disabled = not available or content_edit.text.strip_edges().is_empty() or content_edit.text.length() > 500

func _unhandled_key_input(event: InputEvent) -> void:
	if visible and event.is_action_pressed("ui_cancel"):
		if stage_selecting:
			_set_stage_selection(false)
		else:
			hide()
		get_viewport().set_input_as_handled()
