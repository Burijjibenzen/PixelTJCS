class_name NPC
extends CharacterBody2D

signal arrived(npc)
signal floor_changed(npc)

enum S { IDLE, WANDER, WALK }
const SPEED := 55.0

# 后端处理与短暂离场停留期间暂停移动；气泡结束事件先于恢复行走。
var social_busy := false
var position_held := false
var encounter_wait := ""
var on_stage := true
var cast_version := 0

var npc_id := ""
var npc_name := "?"
var home_map_id := Game.FLOOR3
var home_room := ""
var map_id := Game.FLOOR3
var goal_map_id := Game.FLOOR3
var goal_room := ""
var state: int = S.IDLE
var path := PackedVector2Array()
var path_i := 0
var _sections: Array = []
var _section_i := 0
var _wander_timer := 1.0
var _blocked_time := 0.0

@onready var sprite: AnimatedSprite2D = $Sprite
@onready var label: Label = $NameLabel

func setup(id: String, display_name: String, char_id: int, home: String, tint: Color, home_map: String = Game.FLOOR3) -> void:
	npc_id = id
	npc_name = display_name
	home_room = home
	home_map_id = home_map
	map_id = home_map
	goal_map_id = home_map
	sprite.sprite_frames = load("res://scenes/char_%d.tres" % char_id)
	sprite.modulate = tint
	label.text = display_name

func is_travelling() -> bool:
	return not _sections.is_empty()

func movement_locked() -> bool:
	return not on_stage or social_busy or position_held or not encounter_wait.is_empty()

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
	var planned := Game.route(map_id, global_position, target_map, target)
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

func _physics_process(delta: float) -> void:
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
