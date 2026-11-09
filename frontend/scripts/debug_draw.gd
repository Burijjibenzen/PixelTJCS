extends Node2D
## 区域调试视图: 独立节点, 在场景树中位于 Map/Actors 之后 -> 绘制在最上层。
## (曾画在 main 根节点: 根节点先于子节点渲染, 调试框被地图瓦片完全遮挡)

var enabled := false
var npcs: Array = []


func set_enabled(v: bool) -> void:
	enabled = v
	visible = v
	queue_redraw()


## main.gd 装配后注入 NPC 引用(画目标线用)
func setup(npcs: Array) -> void:
	npcs = npcs


func _process(_delta: float) -> void:
	if enabled:
		queue_redraw()  # 目标线随 NPC 移动实时刷新


func _draw() -> void:
	var colors := [Color(0.3, 0.7, 1.0), Color(1.0, 0.6, 0.3),
		Color(0.4, 1.0, 0.5), Color(1.0, 0.9, 0.3)]
	var font := ThemeDB.fallback_font
	var ci := 0
	for rid in Game.rooms:
		var r: Dictionary = Game.rooms[rid]
		var col: Color = colors[ci % colors.size()]
		ci += 1
		for cell: Vector2i in Game.floor_cells[rid]:
			draw_rect(Rect2(Game.world_corner(cell), Vector2.ONE * Game.tile_size), Color(col.r, col.g, col.b, 0.28))
		var tl := Game.nav_world_of(rid)
		var label := "%s %s [%s]" % [rid, r.name, r.type]
		draw_string(font, tl + Vector2(5, 19), label,
			HORIZONTAL_ALIGNMENT_LEFT, -1, 16, Color(0, 0, 0, 0.9))
		draw_string(font, tl + Vector2(4, 18), label,
			HORIZONTAL_ALIGNMENT_LEFT, -1, 16, Color(1, 1, 1, 1))
	for cell: Vector2i in Game.door_cells:
		draw_rect(Rect2(Game.world_corner(cell) + Vector2(3, 3), Vector2(10, 10)), Color(1, 0.9, 0.2, 0.65))
	# NPC 目标线加粗 + 终点圆点
	for npc in npcs:
		if not is_instance_valid(npc):
			continue
		if npc.path.size() > npc.path_i:
			var end: Vector2 = npc.path[npc.path.size() - 1]
			draw_line(npc.global_position, end, Color(1, 1, 0, 0.6), 3.0)
			draw_circle(end, 5.0, Color(1, 1, 0, 0.9))
