extends CanvasLayer
## 独立楼层浏览器。试走使用各楼层自己的网格，主场景的 NPC 保留在三楼。

const IDS := ["jishilou_floor3", "jishilou_floor4", "zhixinguan_floor2"]
const NAMES := ["济事楼 · 三楼", "济事楼 · 四楼", "智信馆 · 二楼"]
const TILE := 16
const NO_CELL := Vector2i(-1, -1)
const MapDoors := preload("res://scripts/map_doors.gd")
const VIEW_HINT := "点击房间或交互点　右键拖动　滚轮缩放　1/2/3 切换楼层　Esc 返回"
const WALK_HINT := "方向键 / WASD 试走　E 操作身边的门、楼梯或物品　点击空地可从该处开始试走"

@onready var stage: Control = $Background/Stage
@onready var world: Node2D = $Background/Stage/World
@onready var marks: Node2D = $Background/Stage/World/Marks
@onready var walker: AnimatedSprite2D = $Background/Stage/World/Walker
@onready var bar: HBoxContainer = $Background/Toolbar
@onready var status: Label = $Background/Status

var current_floor := -1
var map_node: Node2D
var manifest: Dictionary
var grid: Dictionary
var selected_layer: TileMapLayer
var walk_cell := NO_CELL
var walk_starts: Dictionary[Vector2i, bool] = {}
var step_delay := 0.0
var doors: MapDoors
var open_doors := {}  # 按楼层与门 id 保存，切换楼层后仍有效。
var point_display_by_floor := {}
var previous_pause := false
var active := false

func _ready() -> void:
	for label in NAMES:
		bar.get_node("Floor").add_item(label)
	bar.get_node("Floor").item_selected.connect(show_floor)
	bar.get_node("Points").toggled.connect(func(_on: bool): marks.queue_redraw())
	bar.get_node("Walk").toggled.connect(_walk_mode)
	bar.get_node("Fit").pressed.connect(fit)
	bar.get_node("Back").pressed.connect(close)
	stage.gui_input.connect(_stage_input)
	stage.resized.connect(_stage_resized)
	marks.draw.connect(_draw_marks)
	walker.sprite_frames = preload("res://scenes/char_1.tres")
	walker.play("idle")
	if visible:
		bar.get_node("Back").text = "关闭"
		open.call_deferred()
	elif "--maps" in OS.get_cmdline_user_args():
		open.call_deferred()

func open() -> void:
	if active:
		return
	active = true
	previous_pause = get_tree().paused
	get_tree().paused = true
	visible = true
	if current_floor < 0:
		show_floor(0)

func close() -> void:
	if not active:
		return
	active = false
	if get_tree().current_scene == self:
		get_tree().quit()
		return
	visible = false
	get_tree().paused = previous_pause

func show_floor(index: int, arrival := NO_CELL) -> void:
	if index < 0 or index >= IDS.size():
		return
	var dir := "res://assets/map/%s/" % IDS[index]
	var packed := load(dir + "Map.tscn") as PackedScene
	var meta := Game._load_json(dir + "manifest.json")
	var nav := Game._load_json(dir + "grid.json")
	if not packed or meta.get("rooms", []).is_empty() or nav.get("obstacle", []).is_empty():
		status.text = "无法打开地图资料 · " + NAMES[index]
		bar.get_node("Floor").select(current_floor)
		return
	if map_node:
		world.remove_child(map_node)
		map_node.queue_free()
	selected_layer = null
	manifest = meta
	grid = nav
	map_node = packed.instantiate() as Node2D
	world.add_child(map_node)
	# 这个观察窗口和实际三楼共用物理世界；预览内通行由自己的网格判断。
	for child in map_node.get_children():
		if child is TileMapLayer:
			child.collision_enabled = false
	if current_floor >= 0:
		point_display_by_floor[current_floor] = bar.get_node("Points").button_pressed
	current_floor = index
	bar.get_node("Points").set_pressed_no_signal(point_display_by_floor.get(index, manifest.get("show_points", true)))
	bar.get_node("Floor").select(index)
	_cache_doors()
	var spawn := _cell(manifest.rooms[0].spawn)
	_cache_walk_starts(spawn)
	walk_cell = arrival if walk_starts.has(arrival) and can_walk(arrival) else spawn
	walker.position = _center(walk_cell)
	walker.visible = bar.get_node("Walk").button_pressed
	_frame_floor()
	if walker.visible or arrival != NO_CELL:
		world.scale = Vector2.ONE * maxf(world.scale.x, 1.15)
		_center_on(walk_cell)
	status.text = "%s　·　%d 个区域　·　%d 个交互点" % [NAMES[index], manifest.rooms.size(), manifest.objects.size()]
	status.tooltip_text = status.text
	marks.queue_redraw()

# 大楼层首次打开时，保持与三楼概览相同的家具显示比例；「全图」仍可看完整布局。
func _frame_floor() -> void:
	fit()
	var focus_room: String = manifest.get("preview_room", "")
	if focus_room.is_empty() or stage.size.x < 24 or stage.size.y < 24:
		return
	var baseline := Vector2(Game.size) * TILE
	var zoom := minf((stage.size.x - 24) / baseline.x, (stage.size.y - 24) / baseline.y)
	if zoom <= world.scale.x:
		return
	for room: Dictionary in manifest.rooms:
		if room.id == focus_room:
			world.scale = Vector2.ONE * zoom
			_center_on(_cell(room.spawn))
			var extent := Vector2(_cell(grid.size)) * TILE * zoom
			for axis in 2:
				if extent[axis] >= stage.size[axis]:
					world.position[axis] = clampf(world.position[axis], stage.size[axis] - extent[axis], 0)
				else:
					world.position[axis] = (stage.size[axis] - extent[axis]) * 0.5
			return

func fit() -> void:
	if not map_node or stage.size.x < 1 or stage.size.y < 1:
		return
	var extent := Vector2(_cell(grid.size)) * TILE
	var zoom := minf((stage.size.x - 24) / extent.x, (stage.size.y - 24) / extent.y)
	world.scale = Vector2.ONE * clampf(zoom, 0.04, 5.0)
	world.position = (stage.size - extent * world.scale) * 0.5

func _stage_resized() -> void:
	if visible and map_node:
		fit()

func _center_on(cell: Vector2i) -> void:
	world.position = stage.size * 0.5 - _center(cell) * world.scale

func _cell(raw: Array) -> Vector2i:
	return Vector2i(int(raw[0]), int(raw[1]))

func _center(cell: Vector2i) -> Vector2:
	return (Vector2(cell) + Vector2.ONE * 0.5) * TILE

func _door_key(item: Dictionary) -> String:
	return "%s/%s" % [IDS[current_floor], item.id]

func _cache_doors() -> void:
	doors = MapDoors.new(map_node.get_node("door"), manifest.objects)
	for item: Dictionary in manifest.objects:
		if item.type == "door" and open_doors.get(_door_key(item), false):
			doors.set_open(item.id, true)

func _use(item: Dictionary) -> void:
	match str(item.type):
		"door":
			var key := _door_key(item)
			var opening := not bool(open_doors.get(key, false))
			if not opening and walker.visible and doors.is_passage(item.id, walk_cell):
				status.text = "请先离开门口再关门"
				return
			open_doors[key] = opening
			doors.set_open(item.id, opening)
			status.text = "%s　·　%s" % [item.name, "已打开，可通过" if opening else "已关闭"]
			marks.queue_redraw()
		"stair":
			var destination := IDS.find(item.get("destination_map", ""))
			if destination >= 0 and item.has("destination_cell"):
				show_floor(destination, _cell(item.destination_cell))
				status.text = "已到达%s的楼梯口" % NAMES[destination]
			else:
				status.text = "%s　·　%s" % [item.name, item.get("text", "通往楼层图以外")]
		_:
			var body := str(item.get("text", ""))
			status.text = str(item.name) if body.is_empty() else "%s　·　%s" % [item.name, body]
	status.tooltip_text = status.text

func _has_walkable_floor(cell: Vector2i) -> bool:
	return Rect2i(Vector2i.ZERO, _cell(grid.size)).has_point(cell) and grid.obstacle[cell.y][cell.x] == "0"

# 可开门的房间也可选为起点；家具和墙围住的空隙不可选。
func _cache_walk_starts(spawn: Vector2i) -> void:
	walk_starts.clear()
	if not _has_walkable_floor(spawn):
		return
	var pending: Array[Vector2i] = [spawn]
	walk_starts[spawn] = true
	while not pending.is_empty():
		var cell: Vector2i = pending.pop_back()
		for direction: Vector2i in [Vector2i.LEFT, Vector2i.RIGHT, Vector2i.UP, Vector2i.DOWN]:
			var neighbor := cell + direction
			if not walk_starts.has(neighbor) and _has_walkable_floor(neighbor):
				walk_starts[neighbor] = true
				pending.append(neighbor)

func can_walk(cell: Vector2i) -> bool:
	if not _has_walkable_floor(cell):
		return false
	for item: Dictionary in manifest.objects:
		if item.type == "door" and not open_doors.get(_door_key(item), false) and doors.is_passage(item.id, cell):
			return false
	return true

func _walk_mode(on: bool) -> void:
	walker.visible = on and map_node != null
	$Background/Hint.text = WALK_HINT if on else VIEW_HINT
	if on and map_node:
		world.scale = Vector2.ONE * maxf(1.15, world.scale.x)
		_center_on(walk_cell)
	elif map_node:
		fit()
	marks.queue_redraw()

func _process(delta: float) -> void:
	if not visible or not walker.visible or not map_node:
		return
	step_delay = maxf(0, step_delay - delta)
	if step_delay > 0:
		return
	var move := Vector2i.ZERO
	var animation := "idle"
	if Input.is_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_A):
		move = Vector2i.LEFT
		animation = "walk_left"
	elif Input.is_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_D):
		move = Vector2i.RIGHT
		animation = "walk_right"
	elif Input.is_key_pressed(KEY_UP) or Input.is_key_pressed(KEY_W):
		move = Vector2i.UP
		animation = "walk_up"
	elif Input.is_key_pressed(KEY_DOWN) or Input.is_key_pressed(KEY_S):
		move = Vector2i.DOWN
		animation = "walk_down"
	if move != Vector2i.ZERO:
		step_delay = 0.13
		if can_walk(walk_cell + move):
			walk_cell += move
			walker.position = _center(walk_cell)
			_center_on(walk_cell)
			marks.queue_redraw()
		else:
			animation = "idle"
	walker.play(animation)

func _near_item(cell: Vector2i, radius: float) -> Dictionary:
	var best := {}
	var distance := radius + 0.001
	for item: Dictionary in manifest.objects:
		var score := Vector2(cell).distance_to(Vector2(_cell(item.cell)))
		if item.type == "door":
			if doors.is_passage(item.id, cell) or doors.is_closed_panel(item.id, cell):
				score = 0.0
		else:
			for point: Array in item.get("cells", []):
				score = minf(score, Vector2(cell).distance_to(Vector2(_cell(point))))
		if score < distance:
			distance = score
			best = item
	return best

func _select_cell(cell: Vector2i) -> void:
	var item := _near_item(cell, 0.8)
	if not item.is_empty():
		_use(item)
		return
	selected_layer = null
	if walker.visible and walk_starts.has(cell) and can_walk(cell):
		walk_cell = cell
		walker.position = _center(cell)
		_center_on(cell)
	for room: Dictionary in manifest.rooms:
		var layer := map_node.get_node_or_null(NodePath(str(room.layer))) as TileMapLayer
		if layer and layer.get_cell_source_id(cell) != -1:
			selected_layer = layer
			status.text = str(room.name) if manifest.get("show_room_names", true) else NAMES[current_floor]
			break
	if not selected_layer:
		status.text = NAMES[current_floor]
	status.tooltip_text = status.text
	marks.queue_redraw()

func _stage_input(event: InputEvent) -> void:
	if not map_node:
		return
	if event is InputEventMouseMotion:
		if event.button_mask & (MOUSE_BUTTON_MASK_RIGHT | MOUSE_BUTTON_MASK_MIDDLE):
			world.position += event.relative
	elif event is InputEventMouseButton and event.pressed:
		var map_pos: Vector2 = (event.position - world.position) / world.scale
		match event.button_index:
			MOUSE_BUTTON_LEFT:
				_select_cell(Vector2i((map_pos / TILE).floor()))
			MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN:
				var factor := 1.22 if event.button_index == MOUSE_BUTTON_WHEEL_UP else 1.0 / 1.22
				world.scale = Vector2.ONE * clampf(world.scale.x * factor, 0.06, 5.0)
				world.position = event.position - map_pos * world.scale
	stage.accept_event()

func _unhandled_key_input(event: InputEvent) -> void:
	if not visible or not event is InputEventKey or not event.pressed or event.echo:
		return
	match event.keycode:
		KEY_1, KEY_2, KEY_3:
			show_floor(int(event.keycode - KEY_1))
		KEY_ESCAPE:
			close()
		KEY_E:
			if walker.visible:
				var item := _near_item(walk_cell, 2.3)
				if not item.is_empty():
					_use(item)
				else:
					status.text = "周围没有可操作的物体"
		_:
			return
	get_viewport().set_input_as_handled()

func _draw_marks() -> void:
	if not map_node:
		return
	if selected_layer:
		for tile in selected_layer.get_used_cells():
			marks.draw_rect(Rect2(Vector2(tile) * TILE, Vector2.ONE * TILE), Color(0.20, 0.76, 0.98, 0.30))
	if bar.get_node("Points").button_pressed:
		for item: Dictionary in manifest.objects:
			var pos := _center(_cell(item.cell))
			var color := Color("#b196f5")
			if item.type == "door":
				color = Color("#61cc8d") if open_doors.get(_door_key(item), false) else Color("#ffad58")
			elif item.type == "stair":
				color = Color("#5acaff")
			marks.draw_circle(pos, 5.5, Color("#25333e"))
			marks.draw_circle(pos, 3.6, color)
	if walker.visible:
		marks.draw_arc(_center(walk_cell), 9.0, 0, TAU, 16, Color("#fff09c"), 2.5)
