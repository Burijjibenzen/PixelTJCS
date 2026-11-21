extends RefCounted
## 一份楼层场景实例内的门。通行格决定何时开门，贴图格决定收起哪些门扇。

const APPROACH_STEPS := 6

var _layer: TileMapLayer
var _panels := {}          # 门 id -> 原始贴图，用于关门时逐格恢复
var _passage_at := {}      # 通行格 -> 门 id
var _clearance := {}       # 等人物完全离开这个范围再关门
var _open := {}

func _init(layer: TileMapLayer, objects: Array) -> void:
	_layer = layer
	for door: Dictionary in objects:
		if door.type != "door":
			continue
		var tiles := {}
		var cells: Array = door.get("cells", [door.cell])
		var center := Vector2i(int(door.cell[0]), int(door.cell[1]))
		var bounds := Rect2i(center, Vector2i.ONE)
		for xy: Array in door.get("passage_cells", cells):
			var cell := Vector2i(int(xy[0]), int(xy[1]))
			_passage_at[cell] = door.id
			bounds = bounds.merge(Rect2i(cell, Vector2i.ONE))
		for xy: Array in door.get("art_cells", cells):
			var cell := Vector2i(int(xy[0]), int(xy[1]))
			var source := layer.get_cell_source_id(cell)
			if source >= 0:
				tiles[cell] = [source, layer.get_cell_atlas_coords(cell), layer.get_cell_alternative_tile(cell)]
				bounds = bounds.merge(Rect2i(cell, Vector2i.ONE))
		_panels[door.id] = tiles
		# 角色图像在脚点上方约四格，向南走出时等头部也离开再关门。
		_clearance[door.id] = bounds.grow_individual(1, 1, 1, 4)

func is_passage(id: String, cell: Vector2i) -> bool:
	return _passage_at.get(cell) == id

func is_closed_panel(id: String, cell: Vector2i) -> bool:
	return not _open.has(id) and _panels[id].has(cell)

func set_open(id: String, opened: bool) -> void:
	if _open.has(id) == opened:
		return
	var tiles: Dictionary = _panels[id]
	for cell: Vector2i in tiles:
		if opened:
			_layer.erase_cell(cell)
		else:
			var tile: Array = tiles[cell]
			_layer.set_cell(cell, tile[0], tile[1], tile[2])
	if opened:
		_open[id] = true
	else:
		_open.erase(id)

func update_for_npcs(npcs: Array[NPC], map_id: String, player_cell: Vector2i = Vector2i(-1, -1)) -> void:
	var needed := {}
	if player_cell.x >= 0:
		for passage: Vector2i in _passage_at:
			if abs(player_cell.x - passage.x) + abs(player_cell.y - passage.y) <= 2:
				needed[_passage_at[passage]] = true
	for npc: NPC in npcs:
		if not npc.on_stage:
			continue
		if npc.map_id != map_id:
			continue
		var feet := Game.cell_of(npc.global_position, map_id)
		if _passage_at.has(feet):
			needed[_passage_at[feet]] = true
		for id: String in _open:
			if _clearance[id].has_point(feet):
				needed[id] = true
		# 只为行进路线上的门提前开门。NPC 沿走廊路过邻近的门时不会触发。
		for i in range(npc.path_i, mini(npc.path.size(), npc.path_i + APPROACH_STEPS + 1)):
			var cell := Game.cell_of(npc.path[i], map_id)
			if _passage_at.has(cell):
				needed[_passage_at[cell]] = true
	for id: String in _open.keys():
		if not needed.has(id):
			set_open(id, false)
	for id: String in needed:
		set_open(id, true)
