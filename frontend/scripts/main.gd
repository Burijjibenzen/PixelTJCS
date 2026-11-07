extends Node2D
## M2.1 演示主场景: 生成 10 NPC, 本地日程驱动
## 决策已确认: 玩家模式默认关闭 (纯 NPC), M4 再加玩家; 镜头自由 + 空格跟随

const NPC_SCENE := preload("res://scenes/npc.tscn")
const ROSTER_PATH := "res://data/npc_roster.json"

var npcs: Array = []
var follow_idx := -1
var demo_clock := 8.0 * 3600.0    # 本地演示时钟(秒), 1 真实秒 = 60 游戏秒
var debug_draw := false
var _sched_timer := 2.0

func _ready() -> void:
	var roster: Array = Game._load_json(ROSTER_PATH).get("npcs", [])
	for r in roster:
		var npc := NPC_SCENE.instantiate()
		$Actors.add_child(npc)
		npc.global_position = Game.nav_world_of(r.home)
		npc.setup(r.id, r.name, int(r.char), r.home,
			Color(r.tint[0], r.tint[1], r.tint[2]))
		npc.arrived.connect(_on_npc_arrived)
		npcs.append(npc)
	print("[Main] 已生成 %d 个 NPC" % npcs.size())

func _process(delta: float) -> void:
	demo_clock += delta * 60.0
	($UI/ClockLabel as Label).text = _clock_str() + " · 本地演示时钟"
	_sched_timer -= delta
	if _sched_timer <= 0.0:
		_sched_timer = randf_range(4.0, 7.0)
		_random_schedule()

func _clock_str() -> String:
	var t := int(demo_clock) % 86400
	return "%02d:%02d" % [t / 3600, (t % 3600) / 60]

## 本地演示日程: 随机挑一个 NPC, 50% 回家, 50% 去随机房间
## (M2.2 将替换为后端 /world/state 驱动, npc.go_to_room 接口不变)
func _random_schedule() -> void:
	if npcs.is_empty():
		return
	var npc = npcs[randi() % npcs.size()]
	var rid: String
	if randf() < 0.5:
		rid = npc.home_room
	else:
		var ids: Array = Game.all_room_ids()
		rid = ids[randi() % ids.size()]
	npc.go_to_room(rid)
	print("[日程] %s -> %s" % [npc.npc_name, Game.room_name(rid)])

func _on_npc_arrived(npc) -> void:
	print("[到达] %s 进入 %s (%s)" % [npc.npc_name,
		Game.room_name(npc.goal_room), Game.room_type(npc.goal_room)])
	if debug_draw:
		queue_redraw()

## 空格循环跟随
func cycle_follow():
	if npcs.is_empty():
		return null
	follow_idx = (follow_idx + 1) % npcs.size()
	var npc = npcs[follow_idx]
	$Cam.set_target(npc)
	($UI/InfoLabel as Label).text = "镜头跟随: " + npc.npc_name
	return npc

func _unhandled_key_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_F3:
			debug_draw = not debug_draw
			($UI/InfoLabel as Label).text = "镜头: 自由"
			$Cam.set_target(null)
			queue_redraw()

func _draw() -> void:
	if not debug_draw:
		return
	var colors := [Color(0.3, 0.7, 1.0, 0.6), Color(1.0, 0.6, 0.3, 0.6),
		Color(0.4, 1.0, 0.5, 0.6), Color(1.0, 0.9, 0.3, 0.6)]
	var ci := 0
	for rid in Game.rooms:
		var r: Dictionary = Game.rooms[rid]
		var bb: Array = r.bbox
		var tl := Game.world_corner(Vector2i(bb[0], bb[1]))
		var br := Game.world_corner(Vector2i(bb[2] + 1, bb[3] + 1))
		var col: Color = colors[ci % colors.size()]
		ci += 1
		draw_rect(Rect2(tl, br - tl), col, false, 2.0)
		draw_string(ThemeDB.fallback_font, tl + Vector2(4, 14),
			"%s %s [%s]" % [rid, r.name, r.type],
			HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Color(1, 1, 1, 0.95))
	for npc in npcs:
		if npc.path.size() > npc.path_i:
			draw_line(npc.global_position, npc.path[npc.path.size() - 1],
				Color(1, 1, 0, 0.35), 1.5)
			draw_circle(npc.path[npc.path.size() - 1], 4.0,
				Color(1, 1, 0, 0.7))
