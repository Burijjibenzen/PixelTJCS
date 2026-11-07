class_name NPC
extends CharacterBody2D
## NPC 身体: AStarGrid2D 寻路 + IDLE/WANDER/WALK 状态机
## M2.1: 本地日程驱动; M2.2: 由后端 /world 指令驱动 (接口不变)

signal arrived(npc)

enum S { IDLE, WANDER, WALK }

const SPEED := 55.0
const ARRIVE_DIST := 4.0

var npc_id := ""
var npc_name := "?"
var home_room := ""
var goal_room := ""
var state: int = S.IDLE
var path := PackedVector2Array()
var path_i := 0
var _wander_timer := 1.0
var _stuck_time := 0.0

@onready var sprite: AnimatedSprite2D = $Sprite
@onready var label: Label = $NameLabel

func setup(id: String, display_name: String, char_id: int, home: String,
		tint: Color) -> void:
	npc_id = id
	npc_name = display_name
	home_room = home
	sprite.sprite_frames = load("res://scenes/char_%d.tres" % char_id)
	sprite.modulate = tint
	label.text = display_name

func _ready() -> void:
	label.text = npc_name

## 对外接口: 前往某房间 (后端指令直译)
func go_to_room(rid: String) -> void:
	goal_room = rid
	_set_path(Game.nav_world_of(rid))

## 对外接口: 前往任意世界坐标
func go_to(world_pos: Vector2) -> void:
	goal_room = Game.room_id_at(world_pos)
	_set_path(world_pos)

func _physics_process(delta: float) -> void:
	match state:
		S.IDLE:
			_wander_timer -= delta
			if _wander_timer <= 0.0:
				state = S.WANDER
		S.WANDER:
			var rid := Game.room_id_at(global_position)
			if rid == "":
				rid = goal_room
			if rid != "":
				_set_path(Game.random_world_in(rid))
			else:
				state = S.IDLE
				_wander_timer = 2.0
		S.WALK:
			_step_move(delta)
	_update_anim()

func _step_move(delta: float) -> void:
	if path_i >= path.size():
		_arrived()
		return
	var target := path[path_i]
	var d := target - global_position
	if d.length() < ARRIVE_DIST:
		path_i += 1
		if path_i >= path.size():
			_arrived()
		return
	velocity = d.normalized() * SPEED
	move_and_slide()
	if get_slide_collision_count() > 0:
		_stuck_time += delta
		if _stuck_time > 0.6:          # 卡住: 以当前路点重寻路
			_stuck_time = 0.0
			_set_path(target)
	else:
		_stuck_time = 0.0

func _arrived() -> void:
	path = PackedVector2Array()
	state = S.IDLE
	_wander_timer = randf_range(1.5, 4.0)
	_play("idle")
	arrived.emit(self)

func _set_path(target: Vector2) -> void:
	path = Game.path_between(global_position, target)
	path_i = 0
	_stuck_time = 0.0
	if path.is_empty():
		state = S.IDLE
		_wander_timer = randf_range(1.0, 2.5)
		_play("idle")
	else:
		state = S.WALK

func _update_anim() -> void:
	if state == S.WALK and velocity.length() > 1.0:
		_play(_walk_anim())

func _walk_anim() -> String:
	if absf(velocity.x) > absf(velocity.y):
		return "walk_right" if velocity.x > 0 else "walk_left"
	return "walk_down" if velocity.y > 0 else "walk_up"

func _play(anim: String) -> void:
	if sprite.sprite_frames != null and sprite.animation != StringName(anim):
		sprite.play(anim)
