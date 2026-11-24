extends Node2D
## 区域与门格只在切楼时重绘，NPC 目标线随角色移动。

var enabled := false
var npcs: Array = []
var map_id := Game.FLOOR3:
	set(value):
		map_id = value
		$Regions.queue_redraw()
		queue_redraw()

@onready var regions := $Regions as Node2D

func _ready() -> void:
	regions.draw.connect(_draw_regions)

func setup(actors: Array) -> void:
	npcs = actors
	regions.queue_redraw()

func set_enabled(on: bool) -> void:
	enabled = on
	visible = on
	if on:
		regions.queue_redraw()
		queue_redraw()

func _process(_delta: float) -> void:
	if enabled:
		queue_redraw()

func _draw_regions() -> void:
	if not enabled:
		return
	var floor_info: Dictionary = Game.floor_data[map_id]
	var colors := [Color(0.3, 0.7, 1.0), Color(1.0, 0.6, 0.3),
		Color(0.4, 1.0, 0.5), Color(1.0, 0.9, 0.3)]
	var font := ThemeDB.fallback_font
	var index := 0
	for rid: String in floor_info.rooms:
		var r: Dictionary = floor_info.rooms[rid]
		var color: Color = colors[index % colors.size()]
		index += 1
		for cell: Vector2i in floor_info.floor_cells[rid]:
			var rect := Rect2(Game.world_corner(cell, map_id), Vector2.ONE * Game.tile_size)
			regions.draw_rect(rect, Color(color, 0.28))
		var pos := Game.nav_world_of(rid, map_id)
		var label := "%s %s [%s]" % [rid, r.name, r.type]
		regions.draw_string(font, pos + Vector2(5, 19), label, HORIZONTAL_ALIGNMENT_LEFT, -1, 16, Color.BLACK)
		regions.draw_string(font, pos + Vector2(4, 18), label, HORIZONTAL_ALIGNMENT_LEFT, -1, 16, Color.WHITE)
	for cell: Vector2i in floor_info.door_cells:
		var pos := Game.world_corner(cell, map_id)
		regions.draw_rect(Rect2(pos + Vector2(3, 3), Vector2(10, 10)), Color(1, 0.9, 0.2, 0.65))

func _draw() -> void:
	for npc: NPC in npcs:
		if not npc.is_visible_in_tree() or npc.map_id != map_id or npc.path_i >= npc.path.size():
			continue
		var end := npc.path[npc.path.size() - 1]
		draw_line(npc.global_position, end, Color(1, 1, 0, 0.6), 3.0)
		draw_circle(end, 5.0, Color(1, 1, 0, 0.9))
