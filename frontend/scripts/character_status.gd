extends Control
signal preview_changed(actor: Node2D)
signal character_selected(actor: Node2D)
signal stage_requested(npc_name: String, on_stage: bool)

const OUTLINE_WIDTH := preload("res://scripts/interaction_marks.gd").OUTLINE_WIDTH
const ACTIVITY_LABELS := {
	"research": "科研中", "library": "查阅资料中", "class": "上课中",
	"exercise": "锻炼中", "seminar": "参加组会中", "admin": "整理资料中",
	"break": "休息中", "visit": "会面中", "meeting": "会面中",
	"baseline": "日常活动中", "manual": "停留中",
}

class CharacterRow extends Button:
	var actor: Node2D
	var name_label: Label
	var location_label: Label
	var status_label: Label
	var content: Control
	var overlay: ColorRect
	var overlay_label: Label
	var offstage := false

	func _draw() -> void:
		if is_hovered():
			var scale_x := (get_viewport().get_final_transform() * get_global_transform_with_canvas()).x.length()
			var width := 2.0 / scale_x
			draw_rect(Rect2(Vector2.ZERO, size).grow(-width * 0.5), Color.WHITE, false, width)

class ExpandHandle extends Button:
	var stroke_width: float

	func _shape() -> PackedVector2Array:
		return PackedVector2Array([Vector2(1, 1), Vector2(size.x - 1, 8),
			Vector2(size.x - 1, size.y - 8), Vector2(1, size.y - 1)])

	func _has_point(point: Vector2) -> bool:
		return Geometry2D.is_point_in_polygon(point, _shape())

	func _draw() -> void:
		var edge := _shape()
		draw_colored_polygon(edge, Color("#dfe7dc"))
		var to_screen := get_viewport().get_final_transform() * get_global_transform_with_canvas()
		var from_screen := to_screen.affine_inverse()
		var width := stroke_width if is_hovered() else 1.0
		var pixel_offset := Vector2.ZERO if is_hovered() else Vector2(0.5, 0.5)
		for index in edge.size():
			edge[index] = from_screen * ((to_screen * edge[index] - pixel_offset).round() + pixel_offset)
		edge.append(edge[0])
		var unit := to_screen.x.length()
		draw_polyline(edge, Color.WHITE if is_hovered() else Color("#7d8e7b"), width / unit)
		var mid := size / 2.0
		draw_polyline(PackedVector2Array([mid + Vector2(-2, -5), mid + Vector2(3, 0),
			mid + Vector2(-2, 5)]), Color("#405845"), stroke_width / unit)

var _actors: Array[NPC]
var _player: PlayerAvatar
var _player_ui: CanvasLayer
var _notice_ui: CanvasLayer
var _panel: Control
var _handle: ExpandHandle
var _list: VBoxContainer
var _rows: Array[CharacterRow] = []
var _player_row: CharacterRow
var _hovered_actor: Node2D
var _panel_hovered := false
var _refresh_in := 0.0
var _stage_selection := false

func setup(actors: Array[NPC], player: PlayerAvatar, player_ui: CanvasLayer, notice_ui: CanvasLayer) -> void:
	_actors = actors
	_player = player
	_player_ui = player_ui
	_notice_ui = notice_ui

func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	anchor_bottom = 1.0
	offset_top = 90
	offset_right = 248
	offset_bottom = -38
	resized.connect(queue_redraw)
	_panel = MarginContainer.new()
	_panel.mouse_filter = Control.MOUSE_FILTER_STOP
	_panel.mouse_force_pass_scroll_events = false
	add_child(_panel)
	_panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_panel.offset_left = 12
	_panel.add_theme_constant_override("margin_left", 8)
	_panel.add_theme_constant_override("margin_right", 8)
	_panel.add_theme_constant_override("margin_top", 6)
	_panel.add_theme_constant_override("margin_bottom", 8)
	var body := VBoxContainer.new()
	body.mouse_filter = Control.MOUSE_FILTER_IGNORE
	body.add_theme_constant_override("separation", 5)
	_panel.add_child(body)
	var header := HBoxContainer.new()
	header.mouse_filter = Control.MOUSE_FILTER_IGNORE
	body.add_child(header)
	var title := _label("角色状态", 15, Color("#293e31"))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)
	var collapse := Button.new()
	collapse.theme = preload("res://assets/tea_theme.tres")
	collapse.text = "X"
	collapse.tooltip_text = "收起角色状态"
	collapse.custom_minimum_size = Vector2(27, 24)
	collapse.focus_mode = Control.FOCUS_NONE
	collapse.pressed.connect(_set_expanded.bind(false))
	header.add_child(collapse)
	var drag := preload("res://scripts/window_drag.gd").new()
	add_child(drag)
	drag.bind(self, title)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	body.add_child(scroll)
	_list = VBoxContainer.new()
	_list.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_list.add_theme_constant_override("separation", 3)
	scroll.add_child(_list)
	_player_row = _add_row(_player, _player.sprite, "玩家", Color("#74502d"))
	for actor in _actors:
		_add_row(actor, actor.sprite, actor.npc_name, Color("#28372c"))

	_handle = ExpandHandle.new()
	_handle.position = Vector2(0, 8)
	_handle.size = Vector2(28, 44)
	_handle.stroke_width = OUTLINE_WIDTH
	_handle.focus_mode = Control.FOCUS_NONE
	_handle.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	_handle.tooltip_text = "展开角色状态"
	var empty := StyleBoxEmpty.new()
	for style_name in ["normal", "hover", "pressed", "focus"]:
		_handle.add_theme_stylebox_override(style_name, empty)
	_handle.mouse_entered.connect(_handle.queue_redraw)
	_handle.mouse_exited.connect(_handle.queue_redraw)
	_handle.pressed.connect(_set_expanded.bind(true))
	_handle.hide()
	add_child(_handle)
	get_viewport().size_changed.connect(queue_redraw)
	get_viewport().size_changed.connect(_handle.queue_redraw)
	_refresh_rows()

func _label(value: String, font_size: int, color: Color) -> Label:
	var label := Label.new()
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.text = value
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", color)
	label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	return label

func _add_row(actor: Node2D, sprite: AnimatedSprite2D, actor_name: String, color: Color) -> CharacterRow:
	var row := CharacterRow.new()
	row.actor = actor
	row.custom_minimum_size.y = 51
	row.focus_mode = Control.FOCUS_NONE
	row.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	var colors := {"normal": Color("#eaf0e6"), "hover": Color("#d5e3d5"), "pressed": Color("#c5d6c6")}
	for style_name: String in colors:
		var style := StyleBoxFlat.new()
		style.bg_color = colors[style_name]
		style.set_corner_radius_all(4)
		row.add_theme_stylebox_override(style_name, style)
	_list.add_child(row)
	var content := HBoxContainer.new()
	content.mouse_filter = Control.MOUSE_FILTER_IGNORE
	content.add_theme_constant_override("separation", 7)
	row.add_child(content)
	row.content = content
	content.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	content.offset_left = 6
	content.offset_right = -6
	var frame := sprite.sprite_frames.get_frame_texture("idle", 0) as AtlasTexture
	var head := AtlasTexture.new()
	head.atlas = frame.atlas
	# 所有校园角色目前使用 48×70 的站立帧，前 39 像素覆盖头部。
	head.region = Rect2(frame.region.position, Vector2(frame.region.size.x, 39))
	head.filter_clip = true
	var portrait := TextureRect.new()
	portrait.mouse_filter = Control.MOUSE_FILTER_IGNORE
	portrait.custom_minimum_size = Vector2(42, 42)
	portrait.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	portrait.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	portrait.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	portrait.texture = head
	portrait.modulate = sprite.modulate
	content.add_child(portrait)
	var labels := VBoxContainer.new()
	labels.mouse_filter = Control.MOUSE_FILTER_IGNORE
	labels.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	labels.alignment = BoxContainer.ALIGNMENT_CENTER
	labels.add_theme_constant_override("separation", -2)
	content.add_child(labels)
	row.name_label = _label(actor_name, 14, color)
	row.location_label = _label("", 12, Color("#4d6151"))
	row.status_label = _label("", 13, Color("#354e3c"))
	labels.add_child(row.name_label)
	labels.add_child(row.location_label)
	labels.add_child(row.status_label)
	row.overlay = ColorRect.new()
	row.overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.overlay.color = Color(0.84, 0.84, 0.84, 0.88)
	row.add_child(row.overlay)
	row.overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	row.overlay.offset_left = 2
	row.overlay.offset_top = 2
	row.overlay.offset_right = -2
	row.overlay.offset_bottom = -2
	row.overlay_label = _label("", 18, Color("#3b3b3b"))
	row.overlay.add_child(row.overlay_label)
	row.overlay_label.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	row.overlay_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	row.overlay_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	row.overlay.hide()
	row.mouse_entered.connect(row.queue_redraw)
	row.mouse_exited.connect(row.queue_redraw)
	row.pressed.connect(_select_row.bind(row))
	_rows.append(row)
	return row

func _set_expanded(expanded: bool) -> void:
	_panel.visible = expanded
	_handle.visible = not expanded
	if not expanded:
		_set_hover(null)
		_panel_hovered = false
	_handle.position.x = -position.x
	queue_redraw()

func set_stage_selection(enabled: bool) -> void:
	_stage_selection = enabled
	_set_hover(null)
	if enabled:
		_set_expanded(true)
	for row in _rows:
		row.overlay.visible = enabled and row != _player_row and row.is_hovered()

func _select_row(row: CharacterRow) -> void:
	if _stage_selection:
		if row.actor is NPC:
			stage_requested.emit(row.actor.npc_name, not row.actor.on_stage)
		return
	if not row.offstage:
		_hovered_actor = row.actor
	character_selected.emit(row.actor)

func _set_hover(actor: Node2D) -> void:
	if actor != _hovered_actor:
		_hovered_actor = actor
		preview_changed.emit(actor)

func _process(delta: float) -> void:
	if not _panel.visible:
		return
	_refresh_in -= delta
	if _refresh_in <= 0.0:
		_refresh_in = 0.12
		_refresh_rows()
	var hovered := get_viewport().gui_get_hovered_control()
	var over_panel := hovered != null and (hovered == _panel or _panel.is_ancestor_of(hovered))
	if over_panel != _panel_hovered:
		_panel_hovered = over_panel
		queue_redraw()
	for row in _rows:
		row.overlay.visible = _stage_selection and hovered == row and row != _player_row
	_set_hover(hovered.actor if over_panel and hovered is CharacterRow and hovered.visible
		and not hovered.offstage and not _stage_selection else null)

func _refresh_rows() -> void:
	_player_row.visible = _player.visible
	var world_npcs: Dictionary = ApiClient.last_world.get("npcs", {}) if ApiClient.backend_online else {}
	for row in _rows:
		if not row.visible:
			continue
		var actor: Node2D = row.actor
		row.location_label.text = Game.status_location_at(actor.global_position, actor.map_id)
		if actor == _player:
			row.name_label.text = _player_ui.profile_name
			row.status_label.text = _player_status()
		else:
			var npc := actor as NPC
			var offstage := not npc.on_stage
			if row.offstage != offstage:
				row.offstage = offstage
				row.content.modulate = Color("#ababab") if offstage else Color.WHITE
				for kind in ["normal", "hover", "pressed"]:
					var style: StyleBoxFlat = row.get_theme_stylebox(kind).duplicate()
					style.bg_color = Color("#d6d6d6") if offstage else {
						"normal": Color("#eaf0e6"), "hover": Color("#d5e3d5"), "pressed": Color("#c5d6c6")}[kind]
					row.add_theme_stylebox_override(kind, style)
			row.overlay_label.text = "加入" if offstage else "移除"
			row.status_label.text = "已退场" if offstage else _npc_status(npc, world_npcs.get(npc.npc_name, {}))

func _player_status() -> String:
	var partner: String = _player_ui.conversation_npc
	if not partner.is_empty():
		return "与%s交谈" % partner
	if _notice_ui.visible:
		return "阅读公告中"
	return "行走中" if Time.get_ticks_msec() - _player.last_walk_msec < 160 else "待机中"

func _npc_status(npc: NPC, info: Dictionary) -> String:
	var activity: Dictionary = info.get("activity", {})
	var event: Dictionary = info.get("current_event", {})
	var partner: String = info.get("conversation_partner", "")
	if not partner.is_empty():
		return "与%s交谈" % partner
	var source: String = activity.get("source", "")
	var state: String = activity.get("state", "")
	if source == "notice":
		if state == "active":
			return "阅读公告中"
		if state == "waiting":
			return "等待阅读中"
	if source == "player" or _player_ui.conversation_npc == npc.npc_name:
		var player_name: String = _player_ui.profile_name if _player_ui.conversation_npc == npc.npc_name else "玩家"
		return "与%s交谈" % player_name
	var seeker: String = info.get("encounter_wait_for", "")
	if not seeker.is_empty():
		return "等%s过来" % seeker
	match str(event.get("phase", "")):
		"evaluating":
			return "整理结果中"
		"travelling":
			if event.get("kind") == "conversation":
				return "前往寻找%s" % event.get("target_id", "")
		"waiting":
			if event.get("kind") == "conversation" and event.get("participants", []).size() == 1:
				return "等待与%s交谈" % event.get("target_id", "")
	if npc.social_busy:
		return "处理中"
	if npc.is_travelling():
		var destination := "告示栏" if source == "notice" else Game.status_room_name(npc.goal_map_id, npc.goal_room)
		return "前往%s" % destination
	if state == "active":
		return ACTIVITY_LABELS.get(activity.get("kind", ""), str(activity.get("title", "活动")) + "中")
	if event.get("kind") == "idle":
		return "等待中"
	return "闲逛中" if npc.state == NPC.S.WANDER else "待机中"

func _draw() -> void:
	if not is_instance_valid(_panel) or not _panel.visible:
		return
	# 分数倍窗口缩放下，先对齐屏幕像素边缘，再画固定宽度的白边。
	var to_screen := get_viewport().get_final_transform() * get_global_transform_with_canvas()
	var from_screen := to_screen.affine_inverse()
	var start := from_screen * (to_screen * _panel.position).round()
	var end := from_screen * (to_screen * (_panel.position + _panel.size)).round()
	var rect := Rect2(start, end - start)
	var width := (OUTLINE_WIDTH if _panel_hovered else 1.0) / to_screen.x.length()
	draw_rect(rect, Color("#dfe7dc"))
	draw_rect(rect.grow(-width * 0.5), Color.WHITE if _panel_hovered else Color("#819482"), false, width)
