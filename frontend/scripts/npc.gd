class_name NPC
extends CharacterBody2D

signal arrived(npc)
signal floor_changed(npc)

enum S { IDLE, WANDER, WALK }
const SPEED := 55.0
const SPRITE_CELL := Vector2i(48, 70)

# 后端处理与短暂离场停留期间暂停移动；气泡结束事件先于恢复行走。
var social_busy := false
var player_chat_hold := false
var spectator_target := Vector2.INF
var position_held := false
var encounter_wait := ""
var on_stage := true:
	set(value):
		on_stage = value
		_update_map_visibility()
var cast_version := 0

var npc_id := ""
var npc_name := "?"
var home_map_id := Game.FLOOR3
var home_room := ""
var map_id := Game.FLOOR3:
	set(value):
		map_id = value
		_update_map_visibility()
var viewed_map_id := "":
	set(value):
		viewed_map_id = value
		_update_map_visibility()
var goal_map_id := Game.FLOOR3
var goal_room := ""
var state: int = S.IDLE
var path := PackedVector2Array()
var path_i := 0
var _sections: Array = []
var _section_i := 0
var _wander_timer := 1.0
var _blocked_time := 0.0
var _spectator_origin := Vector2.ZERO
var _spectator_duration := 1.0
var _spectator_sample_age := 1.0

@onready var sprite: AnimatedSprite2D = $Sprite
@onready var label: Label = $NameLabel

func _update_map_visibility() -> void:
	visible = on_stage and (viewed_map_id.is_empty() or map_id == viewed_map_id)

func setup(id: String, display_name: String, char_id: int, home: String, tint: Color,
		home_map: String = Game.FLOOR3, sheet_file: String = "") -> void:
	npc_id = id
	npc_name = display_name
	home_room = home
	home_map_id = home_map
	map_id = home_map
	goal_map_id = home_map
	var frames := _sheet_frames(sheet_file) if not sheet_file.is_empty() else null
	sprite.sprite_frames = frames if frames else load("res://scenes/char_%d.tres" % char_id)
	sprite.play("idle")
	sprite.modulate = tint
	label.text = display_name

static func _sheet_frames(file_name: String) -> SpriteFrames:
	var sheet := load("res://assets/characters/" + file_name) as Texture2D
	if sheet == null or sheet.get_size() != Vector2(SPRITE_CELL * Vector2i(6, 5)):
		push_error("NPC 精灵图需要 6 列 × 5 行，每格 48 × 70 像素：" + file_name)
		return null
	var frames := SpriteFrames.new()
	frames.remove_animation("default")
	var directions := ["down", "left", "right", "up"]
	for index in range(directions.size()):
		_add_sheet_animation(frames, "idle_" + directions[index], sheet, index, 0, 1)
		_add_sheet_animation(frames, "walk_" + directions[index], sheet, 0, index + 1, 6)
	frames.add_animation("idle")
	frames.set_animation_speed("idle", 5.0)
	frames.add_frame("idle", frames.get_frame_texture("idle_down", 0))
	return frames

static func _add_sheet_animation(frames: SpriteFrames, animation: StringName, sheet: Texture2D,
		column: int, row: int, count: int) -> void:
	frames.add_animation(animation)
	frames.set_animation_speed(animation, 5.0)
	for index in range(count):
		var frame := AtlasTexture.new()
		frame.atlas = sheet
		frame.region = Rect2(Vector2(Vector2i(column + index, row) * SPRITE_CELL), Vector2(SPRITE_CELL))
		frame.filter_clip = true
		frames.add_frame(animation, frame)

func is_travelling() -> bool:
	return not _sections.is_empty()

func movement_locked() -> bool:
	return not on_stage or social_busy or player_chat_hold or position_held or not encounter_wait.is_empty()

func hold_position(held: bool) -> void:
	if held and not position_held:
		_sections.clear()
		goal_room = ""
		_idle()
	position_held = held

func reset_to(floor_id: String, room: String) -> void:
	_sections.clear()
	_idle()
	social_busy = false
	player_chat_hold = false
	position_held = false
	encounter_wait = ""
	map_id = floor_id
	goal_map_id = floor_id
	goal_room = room
	global_position = Game.nav_world_of(room, floor_id)
	reset_physics_interpolation()

func hit_test(viewport_position: Vector2) -> bool:
	# 按人物画面和姓名判断点击，不局限于脚下的小碰撞体；兼容镜头缩放。
	var texture := sprite.sprite_frames.get_frame_texture(sprite.animation, sprite.frame)
	if texture:
		var size := texture.get_size()
		var origin := sprite.offset - (size * 0.5 if sprite.centered else Vector2.ZERO)
		var point := sprite.get_global_transform_with_canvas().affine_inverse() * viewport_position
		if Rect2(origin, size).grow(3.0).has_point(point):
			return true
	var label_point := label.get_global_transform_with_canvas().affine_inverse() * viewport_position
	return Rect2(Vector2.ZERO, label.size).has_point(label_point)

func go_to_room(rid: String) -> void:
	go_to_location(map_id, rid)

func go_to_location(target_map: String, rid: String) -> bool:
	if not Game.has_room(target_map, rid):
		return false
	var target := Game.nav_world_of(rid, target_map)
	return _start_trip(target_map, rid, target)

func go_to(world_pos: Vector2) -> bool:
	var rid := Game.room_id_at(world_pos, map_id)
	if not rid.is_empty():
		return _start_trip(map_id, rid, world_pos)
	return false

func _start_trip(target_map: String, rid: String, target: Vector2) -> bool:
	if movement_locked():
		return false
	var origin := global_position
	var planned := Game.route(map_id, origin, target_map, target)
	var cell := Game.cell_of(origin, map_id)
	if planned.is_empty() and Game.is_blocked(cell, map_id):
		# A restored position or collision slide can leave the feet on a blocked tile.
		var room := Game.room_id_at(origin, map_id)
		var best_distance := INF
		var restart_at := origin
		for step: Vector2i in [Vector2i.UP, Vector2i.DOWN, Vector2i.LEFT, Vector2i.RIGHT]:
			var neighbor := cell + step
			if Game.is_blocked(neighbor, map_id):
				continue
			var point := Game.world_of(neighbor, map_id)
			var distance := origin.distance_squared_to(point)
			if distance >= best_distance or (not room.is_empty() and Game.room_id_at(point, map_id) != room):
				continue
			var recovery := Game.route(map_id, point, target_map, target)
			if not recovery.is_empty():
				best_distance = distance
				planned = recovery
				restart_at = point
		if not planned.is_empty():
			global_position = restart_at
			reset_physics_interpolation()
	if planned.is_empty():
		return false
	goal_map_id = target_map
	goal_room = rid
	_sections = planned
	_section_i = 0
	_start_section()
	if path.size() > 1 and (global_position - path[0]).dot(path[1] - path[0]) > 0.0:
		if not test_move(global_transform, path[1] - global_position):
			path_i = 1
	return true

func _start_section() -> void:
	path = _sections[_section_i].path
	path_i = 0
	_blocked_time = 0.0
	state = S.WALK

func apply_spectator_position(info: Dictionary) -> void:
	var sample: Dictionary = info.get("position", {})
	var floor: String = sample.get("map_id", info.get("map_id", ""))
	if not Game.floor_data.has(floor):return
	var point := Game.map_offset(floor) + Vector2(float(sample.get("x", 0)), float(sample.get("y", 0)))
	if sample.is_empty():
		var room: String = info.get("room_id", "")
		if not Game.has_room(floor, room):return
		point = Game.nav_world_of(room, floor)
	if spectator_target == Vector2.INF or map_id != floor:
		var room := Game.room_id_at(point, floor)
		if room.is_empty():
			room = str(Game.all_room_ids(floor)[0])
		reset_to(floor, room)
		goal_room = ""
		global_position = point
		_spectator_origin = point
		_spectator_duration = 1.0
		_spectator_sample_age = 0.0
		reset_physics_interpolation()
		floor_changed.emit(self)
	elif spectator_target != point:
		_spectator_origin = global_position
		_spectator_duration = clampf(_spectator_sample_age, 0.25, 1.5)
		_spectator_sample_age = 0.0
	spectator_target = point

func _physics_process(delta: float) -> void:
	if not ApiClient.can_drive_npcs():
		velocity = Vector2.ZERO
		if spectator_target != Vector2.INF:
			_spectator_sample_age += delta
			var previous := global_position
			var progress := minf(_spectator_sample_age / _spectator_duration, 1.0)
			global_position = _spectator_origin.lerp(spectator_target, progress)
			velocity = (global_position - previous) / delta
		_play(_walk_anim() if velocity.length_squared() > 64.0 else "idle")
		return
	spectator_target = Vector2.INF
	var rate := ApiClient.time_scale()
	sprite.speed_scale = rate
	# M3: 不调用 _idle()，避免清掉正在行走的路线。
	if movement_locked() or rate <= 0.0:
		velocity = Vector2.ZERO
		_play("idle")
		return
	if state == S.IDLE:
		_wander_timer -= delta * rate
		if _wander_timer <= 0.0:
			_begin_wander()
	else:
		_step_move(delta, rate)

func _begin_wander() -> void:
	_wander_timer = randf_range(1.5, 4.0)
	var rid := Game.room_id_at(global_position, map_id)
	if rid.is_empty():
		return
	var stroll := Game.path_between(global_position, Game.random_world_in(rid, map_id), map_id)
	if stroll.size() <= 1:
		return
	# 房间内闲逛不借道走廊，也不触发任务到达上报。
	for point in stroll:
		if Game.room_id_at(point, map_id) != rid:
			return
	path = stroll
	path_i = 0
	state = S.WANDER

func _step_move(delta: float, rate: float = 1.0) -> void:
	var distance_left := SPEED * delta * rate
	# 倍速时允许一帧通过多个格心，仍逐格做物理移动，避免穿墙或倍率失真。
	while not path.is_empty():
		var direction := path[path_i] - global_position
		if direction.length_squared() < 0.01:
			path_i += 1
			if path_i == path.size():
				_finish_section()
			continue
		if distance_left <= 0.001:
			break
		var step := minf(direction.length(), distance_left)
		velocity = direction.normalized() * step / delta
		var previous := global_position
		move_and_slide()
		_play(_walk_anim())
		distance_left -= step
		if previous.distance_squared_to(global_position) < 0.0001:
			_blocked_time += delta
			if _blocked_time >= 1.0:
				_sections.clear()
				goal_room = ""
				_idle()
			break
		_blocked_time = 0.0

func _finish_section() -> void:
	if state == S.WANDER:
		_idle()
		return
	var section: Dictionary = _sections[_section_i]
	if section.has("dest"):
		map_id = section.dest
		global_position = section.landing
		reset_physics_interpolation()
		_section_i += 1
		_start_section()
		floor_changed.emit(self)
		return
	_sections.clear()
	_idle()
	if map_id == goal_map_id and Game.room_id_at(global_position, map_id) == Game.actual_room(goal_map_id, goal_room):
		arrived.emit(self)
	else:
		goal_room = ""

func _idle() -> void:
	path = PackedVector2Array()
	path_i = 0
	velocity = Vector2.ZERO
	state = S.IDLE
	_wander_timer = randf_range(1.5, 4.0)
	_blocked_time = 0.0
	_play("idle")

func _walk_anim() -> String:
	if absf(velocity.x) > absf(velocity.y):
		return "walk_right" if velocity.x > 0 else "walk_left"
	return "walk_down" if velocity.y > 0 else "walk_up"

func _play(anim: String) -> void:
	if sprite.sprite_frames != null and sprite.animation != StringName(anim):
		sprite.play(anim)
