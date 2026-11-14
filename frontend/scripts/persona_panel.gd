extends VBoxContainer
## 公开角色资料页。只展示白名单字段，不读取原始人格卡或模型提示词。

const MUTED := Color(0.70, 0.76, 0.84)
const ACCENT := Color(0.52, 0.86, 0.81)
var actors: Array = []
var backend_url := ""
var selected_name := ""
var request_name := ""
var cache := {}
var pending := false
var attempted_name := ""
var http: HTTPRequest
var state_label: Label
var refresh_button: Button
var portrait: TextureRect
var name_label: Label
var title_label: Label
var role_label: Label
var traits_row: HFlowContainer
var expertise_label: Label
var short_goal: Label
var long_goal: Label
var style_label: Label
var location_label: Label
var destination_label: Label
var activity_label: Label
var schedule_box: VBoxContainer
var _profile := {}
var _was_visible := false
var _last_online := false
var _message := ""
var _location_timer := 0.0


func setup(npcs: Array) -> void:
	actors = npcs


func _ready() -> void:
	if backend_url.is_empty():
		backend_url = ApiClient.BACKEND
	add_theme_constant_override("separation", 8)
	http = HTTPRequest.new()
	http.timeout = 4.0
	add_child(http)
	http.request_completed.connect(_profile_done)
	var toolbar := HBoxContainer.new()
	add_child(toolbar)
	state_label = _label("选择角色查看公开资料", 12, MUTED)
	state_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toolbar.add_child(state_label)
	refresh_button = Button.new()
	refresh_button.text = "刷新资料"
	refresh_button.tooltip_text = "只查询公开人格和位置，不调用模型"
	refresh_button.pressed.connect(refresh)
	toolbar.add_child(refresh_button)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(scroll)
	var body := VBoxContainer.new()
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_theme_constant_override("separation", 12)
	scroll.add_child(body)
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 14)
	body.add_child(header)
	var portrait_frame := PanelContainer.new()
	portrait_frame.add_theme_stylebox_override("panel", _surface(Color(0.13, 0.19, 0.25)))
	header.add_child(portrait_frame)
	portrait = TextureRect.new()
	portrait.custom_minimum_size = Vector2(64, 76)
	portrait.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	portrait.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	portrait.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	portrait_frame.add_child(portrait)
	var identity := VBoxContainer.new()
	identity.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	identity.add_theme_constant_override("separation", 4)
	header.add_child(identity)
	name_label = _label("", 22, Color.WHITE)
	identity.add_child(name_label)
	role_label = _label("", 12, ACCENT)
	identity.add_child(role_label)
	title_label = _label("", 14, MUTED)
	identity.add_child(title_label)
	traits_row = HFlowContainer.new()
	traits_row.add_theme_constant_override("h_separation", 6)
	traits_row.add_theme_constant_override("v_separation", 6)
	body.add_child(traits_row)
	location_label = _label("", 14, ACCENT)
	body.add_child(location_label)
	destination_label = _label("", 12, MUTED)
	body.add_child(destination_label)
	activity_label = _label("", 13, ACCENT)
	body.add_child(activity_label)
	body.add_child(HSeparator.new())
	expertise_label = _section(body, "擅长领域")
	short_goal = _section(body, "近期目标")
	long_goal = _section(body, "长期目标")
	style_label = _section(body, "说话风格")
	var schedule_title := _label("日程安排", 13, ACCENT)
	body.add_child(schedule_title)
	schedule_box = VBoxContainer.new()
	schedule_box.add_theme_constant_override("separation", 8)
	body.add_child(schedule_box)
	_render({})
	set_process(true)


func _label(value: String, font_size: int, color: Color) -> Label:
	var label := Label.new()
	label.text = value
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", color)
	return label


func _surface(color: Color) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = color
	style.set_corner_radius_all(6)
	style.set_content_margin_all(8)
	return style


func _section(parent: VBoxContainer, heading: String) -> Label:
	var group := VBoxContainer.new()
	group.add_theme_constant_override("separation", 4)
	parent.add_child(group)
	group.add_child(_label(heading, 13, ACCENT))
	var content := _label("", 14, Color(0.91, 0.94, 0.98))
	group.add_child(content)
	return content


func select_npc(npc_name: String) -> void:
	selected_name = npc_name
	attempted_name = ""
	_message = ""
	_render(cache.get(npc_name, {}))
	_location_timer = 0.0


func _process(delta: float) -> void:
	if not is_visible_in_tree():
		_was_visible = false
		return
	if not _was_visible or ApiClient.backend_online != _last_online:
		attempted_name = ""
		_was_visible = true
		_last_online = ApiClient.backend_online
	_location_timer -= delta
	if _location_timer <= 0.0:
		_location_timer = 0.5
		_render_location()
		_update_state()
	if not pending and ApiClient.backend_online and attempted_name != selected_name:
		refresh()


func refresh() -> void:
	if pending or selected_name.is_empty() or not ApiClient.backend_online:
		_update_state()
		return
	request_name = selected_name
	attempted_name = selected_name
	pending = true
	_message = ""
	_update_state()
	if http.request(backend_url + "/npcs/" + request_name.uri_encode() + "/profile") != OK:
		pending = false
		_message = "资料请求未能发送，可点击刷新重试"
		_update_state()


func _profile_done(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var completed_name := request_name
	pending = false
	var data = JSON.parse_string(body.get_string_from_utf8())
	if result == HTTPRequest.RESULT_SUCCESS and code == 200 and data is Dictionary and str(data.get("name", "")) == completed_name:
		cache[completed_name] = data
		if selected_name == completed_name:
			_message = ""
			_render(data)
	elif selected_name == completed_name:
		_message = "后端未找到该角色" if code == 404 else "公开资料读取失败，可点击刷新重试"
	_update_state()
	# 快速切换角色时，旧响应只进入自己的缓存，不得覆盖当前角色。
	if selected_name != completed_name and is_visible_in_tree():
		refresh()


func _value(data: Dictionary, key: String, fallback := "未填写") -> String:
	var value = data.get(key)
	return fallback if value == null or str(value).strip_edges().is_empty() else str(value)


func _render(data: Dictionary) -> void:
	_profile = data
	name_label.text = selected_name
	title_label.text = _value(data, "title", "连接后端查看完整公开资料")
	var role := str(data.get("role", ""))
	role_label.text = {"student": "学生", "advisor": "导师", "staff": "教务"}.get(role, "校园角色")
	portrait.texture = null
	for actor in actors:
		if actor.npc_name == selected_name:
			var sprite := actor.get_node_or_null("Sprite") as AnimatedSprite2D
			if sprite and sprite.sprite_frames and sprite.sprite_frames.has_animation("idle"):
				portrait.texture = sprite.sprite_frames.get_frame_texture("idle", 0)
				portrait.modulate = sprite.modulate
	portrait.get_parent().visible = portrait.texture != null
	for child in traits_row.get_children():
		traits_row.remove_child(child)
		child.queue_free()
	var traits: Array = data.get("traits", []) if data.get("traits") is Array else []
	traits = traits.duplicate()
	if not str(data.get("mbti", "")).is_empty():
		traits.push_front(str(data.mbti))
	for trait_text in traits:
		var chip := PanelContainer.new()
		chip.custom_minimum_size.x = 88
		chip.add_theme_stylebox_override("panel", _surface(Color(0.14, 0.21, 0.28)))
		var label := _label(str(trait_text), 12, Color(0.83, 0.92, 0.98))
		label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		chip.add_child(label)
		traits_row.add_child(chip)
	traits_row.visible = not traits.is_empty()
	expertise_label.text = _value(data, "expertise")
	var goals: Dictionary = data.get("goals", {}) if data.get("goals") is Dictionary else {}
	short_goal.text = _value(goals, "short_term")
	long_goal.text = _value(goals, "long_term")
	style_label.text = _value(data, "tone")
	if not str(data.get("catchphrase", "")).is_empty():
		style_label.text += "\n口头禅：“" + str(data.catchphrase) + "”"
	for child in schedule_box.get_children():
		schedule_box.remove_child(child)
		child.queue_free()
	var schedule: Array = data.get("schedule", []) if data.get("schedule") is Array else []
	for entry: Dictionary in schedule:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 12)
		var time := _label(str(entry.get("time", "")), 13, ACCENT)
		time.custom_minimum_size.x = 52
		row.add_child(time)
		var place: Dictionary = entry.get("location", {})
		var label := _label(str(place.get("name", "未知地点")), 13, MUTED)
		label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(label)
		schedule_box.add_child(row)
	if schedule.is_empty():
		schedule_box.add_child(_label("未设置日程" if not data.is_empty() else "连接后端即可查看公开目标与日程，无需配置模型密钥。", 13, MUTED))
	_render_location()
	_update_state()


func _place_name(info: Dictionary, target := false) -> String:
	var map_id := str(info.get("target_map_id" if target else "map_id", ""))
	var room_id := str(info.get("target_room_id" if target else "room_id", ""))
	if Game.floor_data.has(map_id) and Game.has_room(map_id, room_id):
		return Game.map_name(map_id) + " · " + Game.room_name(room_id, map_id)
	return ""


func _render_location() -> void:
	var current := ""
	var destination := ""
	activity_label.text = ""
	if ApiClient.backend_online:
		var info: Dictionary = ApiClient.last_world.get("npcs", {}).get(selected_name, {})
		current = _place_name(info)
		destination = _place_name(info, true)
		var activity: Dictionary = info.get("activity", {})
		if not activity.is_empty():
			activity_label.text = "当前活动：%s\n活动状态：%s\n安排依据：%s\n行程说明：%s" % [activity.get("title", "基础作息"), activity.get("state_label", "等待"), activity.get("reason", "基础作息"), activity.get("detail", "等待出发")]
		if current.is_empty():
			current = str(_profile.get("location", {}).get("name", "位置待同步"))
		if destination.is_empty():
			destination = str(_profile.get("destination", {}).get("name", "目的地待同步"))
	else:
		for actor in actors:
			if actor.npc_name == selected_name and actor.get("map_id") != null:
				var map_id := str(actor.get("map_id"))
				var room := Game.room_id_at(actor.global_position, map_id)
				current = _place_name({"map_id": map_id, "room_id": room})
	location_label.text = ("现在在：" if ApiClient.backend_online else "本地位置：") + (current if not current.is_empty() else "位置待同步")
	destination_label.text = "目的地：" + destination + ("（已在目的地）" if destination == current else "") if not destination.is_empty() else ""
	destination_label.visible = not destination_label.text.is_empty()
	activity_label.visible = not activity_label.text.is_empty()


func _update_state() -> void:
	refresh_button.disabled = pending or not ApiClient.backend_online or selected_name.is_empty()
	if not ApiClient.backend_online:
		state_label.text = "后端未连接 · 显示上次同步的公开资料" if cache.has(selected_name) else "后端未连接 · 可查看本地角色与位置"
	elif not _message.is_empty():
		state_label.text = _message
	elif pending:
		state_label.text = "正在同步公开资料…"
	else:
		state_label.text = "公开人格资料 · 位置随世界同步" if cache.has(selected_name) else "等待公开资料同步"
