extends CanvasLayer
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
var refresh_button: Button
var title_edit: LineEdit
var content_edit: TextEdit
var status: Label
var cards: VBoxContainer

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
	var panel := PanelContainer.new()
	panel.name = "Window"
	panel.position = Vector2(280, 55)
	panel.size = Vector2(720, 610)
	var surface := StyleBoxFlat.new()
	surface.bg_color = Color(0.07, 0.10, 0.15, 0.99)
	surface.set_corner_radius_all(8)
	surface.set_content_margin_all(14)
	panel.add_theme_stylebox_override("panel", surface)
	add_child(panel)
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 8)
	panel.add_child(body)
	var heading := HBoxContainer.new()
	body.add_child(heading)
	var title := _label("校园公告栏", 21)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(title)
	var close := Button.new()
	close.text = "关闭"
	close.pressed.connect(hide)
	heading.add_child(close)
	body.add_child(_label("角色靠近公告栏会自行停下阅读；正在交谈时，结束后再读。", 13))
	picker = OptionButton.new()
	body.add_child(picker)
	for map_id in Game.MAP_IDS:
		for board: Dictionary in Game.floor_data[map_id].get("noticeboards", []):
			boards.append(board)
			picker.add_item(str(board.location) + " · 公告栏")
	picker.item_selected.connect(func(index): open_board(str(boards[index].id)))
	status = _label("", 13)
	status.custom_minimum_size.y = 36
	body.add_child(status)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size.y = 200
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	body.add_child(scroll)
	cards = VBoxContainer.new()
	cards.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cards.add_theme_constant_override("separation", 10)
	scroll.add_child(cards)
	body.add_child(HSeparator.new())
	title_edit = LineEdit.new()
	title_edit.placeholder_text = "公告标题（最多80字）"
	title_edit.max_length = 80
	body.add_child(title_edit)
	content_edit = TextEdit.new()
	content_edit.placeholder_text = "公告正文（最多500字）"
	content_edit.custom_minimum_size.y = 76
	content_edit.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	body.add_child(content_edit)
	title_edit.text_changed.connect(func(_text): _update_buttons())
	content_edit.text_changed.connect(_update_buttons)
	var actions := HBoxContainer.new()
	body.add_child(actions)
	publish_button = Button.new()
	publish_button.text = "发布到此公告栏"
	publish_button.pressed.connect(publish)
	actions.add_child(publish_button)
	refresh_button = Button.new()
	refresh_button.text = "刷新公告"
	refresh_button.pressed.connect(refresh)
	actions.add_child(refresh_button)
	read_status = _label("角色靠近后会自动阅读，无需手动安排。", 13)
	body.add_child(read_status)
	_update_buttons()
	hide()

func _label(text: String, font_size: int) -> Label:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", font_size)
	return label

func open_board(key := "") -> void:
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
	picker.select(found)
	show()
	_render(cache.get(key, {}).get("notices", []))
	status.text = "后端未连接，连接后可查看与发布公告。" if not ApiClient.backend_online else "正在读取公告…"
	refresh_timer = 3.0
	_update_buttons()
	refresh()

func _process(delta: float) -> void:
	if not visible:
		return
	_update_buttons()
	if not ApiClient.backend_online:
		status.text = "后端未连接，当前显示缓存公告。" if cache.has(board_id) else "后端未连接，连接后可查看与发布公告。"
		return
	# 输入焦点（包括中文输入法正在组词）或未发布草稿存在时，不自动请求。
	if title_edit.has_focus() or content_edit.has_focus() or not title_edit.text.is_empty() or not content_edit.text.is_empty():
		refresh_timer = 3.0
		return
	refresh_timer -= delta
	if refresh_timer <= 0:
		refresh_timer = 3.0
		refresh()

func refresh() -> void:
	_request("load")

func publish() -> void:
	_request("publish", {"title": title_edit.text.strip_edges(), "content": content_edit.text.strip_edges()})

func _request(kind: String, payload := {}) -> void:
	if pending or board_id.is_empty() or not ApiClient.backend_online:
		return
	pending = true
	operation = kind
	request_board = board_id
	request_draft = {"title": title_edit.text, "content": content_edit.text} if kind == "publish" else {}
	_update_buttons()
	var path := "/noticeboards/" + board_id.uri_encode() + ("/read" if kind == "read" else "/notices")
	var method := HTTPClient.METHOD_GET if kind == "load" else HTTPClient.METHOD_POST
	if http.request(backend_url + path, ["Content-Type: application/json"], method, "" if kind == "load" else JSON.stringify(payload)) != OK:
		pending = false
		status.text = "请求未能发送，请稍后刷新。"
		_update_buttons()

func _done(result: int, code: int, _headers: PackedStringArray, bytes: PackedByteArray) -> void:
	var data = JSON.parse_string(bytes.get_string_from_utf8())
	var completed_board := request_board
	var completed_operation := operation
	var completed_draft := request_draft
	pending = false
	_update_buttons()
	if result != HTTPRequest.RESULT_SUCCESS or code != 200 or not data is Dictionary:
		if board_id == completed_board:
			status.text = str(data.get("detail", "公告读取失败，请刷新重试。")) if data is Dictionary else "连接失败，请检查后端。"
	elif completed_operation == "load":
		var changed: bool = cache.get(completed_board, {}).get("notices", []) != data.get("notices", [])
		cache[completed_board] = data
		if board_id == completed_board:
			if changed:
				_render(data.get("notices", []))
			status.text = "共 %d 条公告 · 发布不会自动通知所有角色" % data.get("notices", []).size()
			var readers: PackedStringArray = []
			for row: Dictionary in data.get("readers", []):
				readers.append(str(row.npc) + "：" + str(row.detail))
			read_status.text = "　".join(readers) if not readers.is_empty() else "角色靠近后会自动阅读，无需手动安排。"
	else:
		if board_id == completed_board:
			status.text = "发布成功" if completed_operation == "publish" else str(data.get("detail", "角色已开始阅读"))
			# 只清空刚刚提交的版本，不能删除请求期间继续输入的新内容。
			if completed_operation == "publish" and title_edit.text == completed_draft.get("title", "") and content_edit.text == completed_draft.get("content", ""):
				title_edit.clear()
				content_edit.clear()
		refresh_timer = 0.5
	if board_id != completed_board:
		refresh()

func _render(notices: Array) -> void:
	for child in cards.get_children():
		cards.remove_child(child)
		child.queue_free()
	if notices.is_empty():
		cards.add_child(_label("暂无公告，可以在下方发布校园通知。", 15))
	for notice: Dictionary in notices:
		var card := VBoxContainer.new()
		card.add_theme_constant_override("separation", 6)
		cards.add_child(card)
		card.add_child(_label(str(notice.get("title", "校园通知")), 18))
		var minutes := maxi(0, int(float(notice.get("published_at", 0))))
		card.add_child(_label("发布于：第 %d 天 %02d:%02d · %s" % [int(minutes / 1440) + 1, int((minutes % 1440) / 60), minutes % 60, picker.get_item_text(picker.selected)], 12))
		card.add_child(_label(str(notice.get("content", "")), 15))
		card.add_child(HSeparator.new())

func _update_buttons() -> void:
	var available := ApiClient.backend_online and not pending and not board_id.is_empty()
	refresh_button.disabled = not available
	publish_button.disabled = not available or title_edit.text.strip_edges().is_empty() or content_edit.text.strip_edges().is_empty() or content_edit.text.length() > 500

func _unhandled_key_input(event: InputEvent) -> void:
	if visible and event.is_action_pressed("ui_cancel"):
		hide()
		get_viewport().set_input_as_handled()
