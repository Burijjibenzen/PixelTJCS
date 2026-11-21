extends VBoxContainer
## 按角色查询持久事件；公告和对话内容只按普通文本绘制。

const REACTIONS := {
	"scheduled": "已安排活动",
	"cancelled": "已取消活动",
	"continue": "继续原计划",
	"historical": "历史记录",
}
const ASSESSMENTS := {
	"pending": "等待角色判断",
	"remembered": "公告正文已保存为记忆",
	"unavailable": "未取得模型判断",
	"timeout": "模型判断超时",
	"invalid": "模型返回格式无效",
	"error": "模型判断失败",
	"stale": "情况已变化，未执行动作",
	"rejected": "动作未通过校验",
}
const CONSENT_LABELS := {"accept": "现在聊", "brief": "简短聊", "defer": "稍后聊", "decline": "婉拒"}

var selected_name := ""
var _request_actor := ""
var _request_cursor := ""
var _next_cursor := ""
var _pending := false
var _dirty := true
var _online := false
var _http: HTTPRequest
var _status: Label
var _current: Label
var _refresh: Button
var _older: Button
var entries: VBoxContainer
var _scroll: ScrollContainer
var _expanded := {}
var _hovered: PanelContainer
var _card_normal := StyleBoxFlat.new()
var _card_hover: StyleBoxFlat
var _field_font := FontVariation.new()

func _ready() -> void:
	_field_font.base_font = get_theme_font("normal_font", "RichTextLabel")
	_field_font.variation_embolden = 0.25
	_http = HTTPRequest.new()
	_http.timeout = 4.0
	add_child(_http)
	_http.request_completed.connect(_done)
	_current = Label.new()
	_current.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_current.add_theme_color_override("font_color", Color("#794522"))
	_current.hide()
	add_child(_current)
	var toolbar := HBoxContainer.new()
	add_child(toolbar)
	_status = Label.new()
	_status.text = "" if ApiClient.backend_online else "连接后端后查看事件"
	_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	toolbar.add_child(_status)
	_refresh = Button.new()
	_refresh.text = "刷新"
	_refresh.pressed.connect(_fetch.bind(""))
	toolbar.add_child(_refresh)
	_older = Button.new()
	_older.text = "加载更早"
	_older.pressed.connect(func(): _fetch(_next_cursor))
	toolbar.add_child(_older)
	_scroll = ScrollContainer.new()
	_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(_scroll)
	entries = VBoxContainer.new()
	entries.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	entries.add_theme_constant_override("separation", 8)
	_scroll.add_child(entries)
	_card_normal.bg_color = Color("#e9ddcf")
	_card_normal.border_color = Color("#c0ab98")
	_card_normal.set_border_width_all(2)
	_card_normal.set_corner_radius_all(6)
	_card_normal.set_content_margin_all(7)
	_card_hover = _card_normal.duplicate() as StyleBoxFlat
	_card_hover.border_color = Color.WHITE
	_update_controls()

func select_npc(npc_name: String) -> void:
	if selected_name == npc_name:
		return
	selected_name = npc_name
	_update_current()
	_expanded.clear()
	_next_cursor = ""
	_clear_entries()
	_status.text = "" if ApiClient.backend_online else "连接后端后查看事件"
	_dirty = true
	_update_controls()

func invalidate(npc_name: String) -> void:
	if selected_name == npc_name:
		_dirty = true

func _process(_delta: float) -> void:
	if _online != ApiClient.backend_online:
		_online = ApiClient.backend_online
		_dirty = true
		_status.text = "" if _online else "连接后端后查看事件"
		_update_controls()
	if is_visible_in_tree() and _dirty and _online and not _pending and not selected_name.is_empty():
		_fetch("")
	if is_visible_in_tree():
		_update_current()
		_update_hover()

func _update_current() -> void:
	var npc: Dictionary = ApiClient.last_world.get("npcs", {}).get(selected_name, {})
	var event: Dictionary = npc.get("current_event", {})
	var title: String = "" if event.get("kind") == "idle" else event.get("title", "")
	if not str(npc.get("conversation_partner", "")).is_empty():
		title = "与%s交谈" % npc.conversation_partner
	elif event.get("phase") == "travelling":
		title = "前往执行 · " + title
	elif event.get("phase") == "evaluating":
		title += " · 整理结果中"
	_current.visible = ApiClient.backend_online and not title.is_empty()
	_current.text = "当前 · " + title if _current.visible else ""

	var reason := str(event.get("schedule_reason", ""))
	if _current.visible and not reason.is_empty():
		_current.text += "\n" + _schedule_label(event.get("tier"), reason)

func _schedule_label(tier: Variant, reason: String) -> String:
	var label: String = {"low": "低档", "medium": "中档", "high": "高档"}.get(tier, "")
	return reason if label.is_empty() else label + " · " + reason

func _clear_entries() -> void:
	_hovered = null
	for card in entries.get_children():
		entries.remove_child(card)
		card.queue_free()

func _update_hover() -> void:
	var control: Node = get_viewport().gui_get_hovered_control()
	while control and control.get_parent() != entries:
		control = control.get_parent()
	var hovered := control as PanelContainer
	if hovered == _hovered:
		return
	if is_instance_valid(_hovered):
		_hovered.add_theme_stylebox_override("panel", _card_normal)
	_hovered = hovered
	if _hovered:
		_hovered.add_theme_stylebox_override("panel", _card_hover)

func _fetch(before: String) -> void:
	if _pending or selected_name.is_empty() or not ApiClient.backend_online:
		return
	_dirty = false
	_request_actor = selected_name
	_request_cursor = before
	var url := ApiClient.backend_url + "/npcs/" + selected_name.uri_encode() + "/timeline"
	if not before.is_empty():
		url += "?before=" + before.uri_encode()
	_pending = _http.request(url) == OK
	_status.text = "正在读取事件…" if _pending else "无法连接后端"
	_update_controls()

func _done(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	_pending = false
	if _request_actor != selected_name:
		_update_controls()
		return
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		_status.text = "事件读取失败，可点击刷新重试"
		_update_controls()
		return
	var data: Variant = JSON.parse_string(body.get_string_from_utf8())
	if not data is Dictionary or data.get("actor") != selected_name or not data.get("events") is Array:
		_status.text = "事件格式不正确"
		_update_controls()
		return
	if _request_cursor.is_empty():
		_clear_entries()
	_next_cursor = str(data.next_cursor) if data.get("next_cursor") != null else ""
	for item: Dictionary in data.events:
		_append(item)
	_status.text = "该角色还没有事件记录" if entries.get_child_count() == 0 else ""
	_update_controls()

func _append(item: Dictionary) -> PanelContainer:
	var title := str(item.content.get("title", item.kind))
	var is_invitation: bool = item.kind == "conversation" or item.kind == "conversation_completed"
	var received: bool = is_invitation and selected_name == str(item.target.id) and selected_name != str(item.actor)
	match str(item.kind):
		"encounter_wait":
			if item.get("phase") == "scheduled":
				title += " · 中档待办"
		"conversation":
			title = "交谈邀约　%s → %s" % [item.actor, item.target.id]
		"notice_read":
			title = "阅读公告"
		"player_conversation":
			title = "与玩家交谈"
		"conversation_completed":
			title = "角色对话　%s ↔ %s" % [item.actor, item.target.id]
	if received:
		title = "收到 %s 的邀约" % item.actor
	var minutes := int(item.game_time)
	var map_id: String = item.location.map_id
	var room_id: String = item.location.room_id
	var place := "地点未记录"
	if Game.floor_data.has(map_id) and Game.has_room(map_id, room_id):
		place = Game.map_name(map_id) + " · " + Game.status_room_name(map_id, room_id)
	var time_text := _game_time(minutes)
	if item.get("ended_at") != null and int(item.ended_at) > minutes:
		time_text += " 至 " + _game_time(int(item.ended_at))

	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", _card_normal)
	entries.add_child(card)
	var body := VBoxContainer.new()
	body.name = "Body"
	card.add_child(body)
	var summary := MarginContainer.new()
	summary.name = "Summary"
	summary.add_theme_constant_override("margin_left", 9)
	summary.add_theme_constant_override("margin_right", 9)
	summary.add_theme_constant_override("margin_top", 4)
	summary.add_theme_constant_override("margin_bottom", 4)
	body.add_child(summary)
	var header := Button.new()
	header.name = "Toggle"
	header.toggle_mode = true
	header.flat = true
	header.alignment = HORIZONTAL_ALIGNMENT_LEFT
	header.text = "▸"
	header.tooltip_text = "展开或收起事件详情"
	summary.add_child(header)
	var summary_fields := RichTextLabel.new()
	summary_fields.name = "Fields"
	summary_fields.mouse_filter = Control.MOUSE_FILTER_IGNORE
	summary_fields.fit_content = true
	summary_fields.scroll_active = false
	summary_fields.add_theme_constant_override("line_separation", 1)
	var inset := MarginContainer.new()
	inset.name = "Inset"
	inset.mouse_filter = Control.MOUSE_FILTER_IGNORE
	inset.add_theme_constant_override("margin_left", 30)
	inset.add_child(summary_fields)
	summary.add_child(inset)
	_field(summary_fields, "行为", title)
	_field(summary_fields, "时间", time_text)
	_field(summary_fields, "地点", place)
	var details := RichTextLabel.new()
	details.name = "Details"
	details.selection_enabled = true
	details.fit_content = true
	details.scroll_active = false
	body.add_child(details)
	var identity: String = item.get("id", "")
	header.set_pressed_no_signal(bool(_expanded.get(identity, false)))
	details.visible = header.button_pressed
	if details.visible:
		header.text = "▾"
	header.toggled.connect(func(open: bool):
		details.visible = open
		header.text = "▾" if open else "▸"
		if not identity.is_empty():
			if open:
				_expanded[identity] = true
			else:
				_expanded.erase(identity)
	)

	var notes: Array = item.content.get("schedule_notes", [])
	var schedule_reason := str(item.content.get("decision_reason", ""))
	if notes.is_empty() and not schedule_reason.is_empty():
		_field(details, "调度", _schedule_label(item.content.get("tier"), schedule_reason))
	if not notes.is_empty():
		for note: Dictionary in notes:
			_field(details, "调度", _schedule_label(item.content.get("tier"), str(note.text)))

	match str(item.kind):
		"notice_read":
			if item.content.has("reading_started_at"):
				_field(details, "到达并开始阅读", _game_time(int(item.content.reading_started_at)))
			if item.result.get("kind") == "notice_learned" and item.get("source") is Dictionary:
				_field(details, "公告内容", str(item.source.get("text", "")))
			var activity: Variant = item.result.get("activity")
			if activity != null and activity.get("appointment_at") != null:
				_field(details, "公告时间", _game_time(int(activity.appointment_at)))
		"conversation", "conversation_completed":
			for line: Dictionary in item.content.get("transcript", []):
				_field(details, str(line.speaker), str(line.text))
	if item.kind == "conversation_completed":
		var relation: Dictionary = item.result.relation
		_field(details, "关系变化", "好感度 %.0f　%s" % [relation.score, relation.reason])
		var gossip: Dictionary = item.result.gossip
		if not gossip.is_empty():
			_field(details, "转述", "%s 从 %s 获得消息" % [gossip.recipient, gossip.source])
	if item.kind == "player_conversation":
		for turn: Dictionary in item.result.get("turns", []):
			for line: Dictionary in turn.transcript:
				_field(details, str(line.speaker), str(line.text))
	if item.content.get("purpose", "") != "" and not received:
		_field(details, "来访目的" if is_invitation else "目的", str(item.content.purpose))
	if item.result.get("detail", "") != "":
		_field(details, "旁听印象" if item.kind == "conversation_overheard" else "执行结果", str(item.result.detail))
	var consent: Variant = item.result.get("consent")
	if consent is Dictionary:
		var label: String = CONSENT_LABELS.get(consent.get("decision"), "")
		var reason: String = consent.get("reason", "")
		_field(details, "我的回应" if received else "对方回应", (label + "　" + reason).strip_edges())
	var assessment: Variant = item.get("assessment")
	if assessment is Dictionary:
		_append_assessment(details, assessment)
	var reaction: Variant = item.get("reaction")
	if reaction != null:
		_field(details, REACTIONS.get(reaction.action, reaction.action), str(reaction.reason))
	if item.get("source_event_id") != null and item.get("source") is Dictionary and item.kind != "notice_read":
		_field(details, "由此触发", str(item.source.get("title", item.source.get("trigger", "前序事件"))))
	if details.get_parsed_text().is_empty():
		_field(details, "详情", "该事件暂无进一步记录")
	return card

func _field(details: RichTextLabel, label: String, value: String) -> void:
	details.push_color(Color("#794522"))
	details.push_font(_field_font)
	details.add_text(label + "   ")
	details.pop()
	details.pop()
	details.add_text(value + "\n")

func _append_assessment(details: RichTextLabel, assessment: Dictionary) -> void:
	var status: String = assessment.get("status", "")
	if assessment.get("outcome", "") != "":
		_field(details, "角色判断", str(assessment.outcome))
		_field(details, "原因", str(assessment.reason))
		var action: Variant = assessment.get("action")
		if action is Dictionary and status == "decided":
			match action.get("type"):
				"talk_to":
					_field(details, "当时的后续决定", "找%s交谈" % action.npc)
				"perform_at":
					var destination: String = assessment.get("destination", "")
					_field(details, "当时的后续决定", str(action.purpose) if destination.is_empty()
						else "前往%s，%s" % [destination, action.purpose])
				"extend":
					_field(details, "当时的后续决定", "继续当前活动 %d 游戏分钟" % int(action.minutes))
		elif action == null and status == "decided":
			_field(details, "当时的后续决定", "继续原安排")
	if ASSESSMENTS.has(status):
		_field(details, "状态", ASSESSMENTS[status])

func _game_time(minutes: int) -> String:
	@warning_ignore("integer_division")
	return "第%d天 %02d:%02d" % [(minutes / 1440) + 1, (minutes % 1440) / 60, minutes % 60]

func _update_controls() -> void:
	_refresh.disabled = _pending or not ApiClient.backend_online or selected_name.is_empty()
	_older.disabled = _refresh.disabled or _next_cursor.is_empty()
