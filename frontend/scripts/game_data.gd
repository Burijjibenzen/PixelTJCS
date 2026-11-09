extends Node
## 地图数据 + 寻路网格 + 房间查询 (autoload: Game)
## 建筑分区由房间地板图层指定；grid.json 只负责寻路障碍。

const MAP_DIR := "res://assets/map/jishilou_floor3"

var tile_size: int = 16
var origin := Vector2i.ZERO          # 地图原点格坐标
var size := Vector2i.ZERO            # 网格宽高(格)
var astar := AStarGrid2D.new()
var rooms := {}
var floor_cells := {}                # 房间的全部地板，供区域显示
var region_cells := {}               # 房间里可走的地板，供 NPC 漫步
var _room_by_cell := {}
var waypoint_rooms := {}             # 旧日程位置点 -> 实际所在房间
var door_cells: Array[Vector2i] = []

func _ready() -> void:
	var grid: Dictionary = _load_json(MAP_DIR + "/grid.json")
	origin = Vector2i(grid.origin[0], grid.origin[1])
	size = Vector2i(grid.size[0], grid.size[1])
	tile_size = int(grid.tile_size)
	var manifest := _load_json(MAP_DIR + "/manifest.json")
	for obj: Dictionary in manifest.objects:
		if obj.type == "door":
			for cell: Array in obj.get("passage_cells", obj.get("cells", [obj.cell])):
				door_cells.append(Vector2i(cell[0], cell[1]))
	for r: Dictionary in manifest.rooms:
		rooms[r.id] = r
		for point: String in r.get("waypoints", {}):
			waypoint_rooms[point] = r.id
	astar.region = Rect2i(0, 0, size.x, size.y)
	astar.cell_size = Vector2(tile_size, tile_size)
	astar.diagonal_mode = AStarGrid2D.DIAGONAL_MODE_NEVER
	astar.default_compute_heuristic = AStarGrid2D.HEURISTIC_MANHATTAN
	astar.default_estimate_heuristic = AStarGrid2D.HEURISTIC_MANHATTAN
	astar.update()
	for y in size.y:
		for x in size.x:
			if grid.obstacle[y][x] == "1":
				astar.set_point_solid(Vector2i(x, y), true)
	print("[Game] 网格 %dx%d 房间 %d 个" % [size.x, size.y, rooms.size()])

func bind_room_layers(map_node: Node2D) -> void:
	_room_by_cell.clear()
	for r: Dictionary in rooms.values():
		var layer: TileMapLayer = map_node.get_node(NodePath(r.layer))
		var cells := layer.get_used_cells()
		floor_cells[r.id] = cells
		var walkable: Array[Vector2i] = []
		for cell in cells:
			_room_by_cell[cell] = r.id
			if not is_blocked(cell):
				walkable.append(cell)
		region_cells[r.id] = walkable

func is_blocked(cell: Vector2i) -> bool:
	return not astar.is_in_boundsv(cell) or astar.is_point_solid(cell)

func cell_of(world: Vector2) -> Vector2i:
	return Vector2i((world / float(tile_size)).floor()) - origin

func world_of(cell: Vector2i) -> Vector2:
	return Vector2(cell + origin) * tile_size + Vector2.ONE * tile_size * 0.5

func world_corner(cell: Vector2i) -> Vector2:
	return Vector2(cell + origin) * tile_size

func path_between(from_world: Vector2, to_world: Vector2) -> PackedVector2Array:
	var a := cell_of(from_world)
	var b := cell_of(to_world)
	if is_blocked(a):
		a = nearest_open(a)
	if is_blocked(b):
		b = nearest_open(b)
	if a == b or not astar.is_in_boundsv(a) or not astar.is_in_boundsv(b):
		return PackedVector2Array()
	var pts := astar.get_point_path(a, b)
	var out := PackedVector2Array()
	var center_off := Vector2.ONE * tile_size * 0.5
	for p in pts:
		out.append(p + Vector2(origin) * tile_size + center_off)
	return out

func nearest_open(cell: Vector2i) -> Vector2i:
	if not is_blocked(cell):
		return cell
	for r in range(1, 6):
		for dx in range(-r, r + 1):
			for dy in range(-r, r + 1):
				var c := cell + Vector2i(dx, dy)
				if astar.is_in_boundsv(c) and not astar.is_point_solid(c):
					return c
	return cell

# ---- 房间查询 ----
func room_id_at(world: Vector2) -> String:
	return _room_by_cell.get(cell_of(world), "")

func room_name(rid: String) -> String:
	return rooms.get(waypoint_rooms.get(rid, rid), {}).get("name", rid)

func room_type(rid: String) -> String:
	return rooms.get(waypoint_rooms.get(rid, rid), {}).get("type", "room")

func nav_world_of(rid: String) -> Vector2:
	var room: Dictionary = rooms.get(waypoint_rooms.get(rid, rid), {})
	var nav: Array = room.get("waypoints", {}).get(rid, room.get("spawn", [0, 0]))
	return world_of(Vector2i(nav[0], nav[1]))

func random_world_in(rid: String) -> Vector2:
	var cells: Array = region_cells.get(waypoint_rooms.get(rid, rid), [])
	if cells.is_empty():
		return nav_world_of(rid)
	return world_of(cells[randi() % cells.size()])

func all_room_ids() -> Array:
	return rooms.keys()

func _load_json(path: String) -> Dictionary:
	var txt := FileAccess.get_file_as_string(path)
	if txt.is_empty():
		push_error("无法读取: " + path)
		return {}
	var data = JSON.parse_string(txt)
	if data == null:
		push_error("JSON 解析失败: " + path)
		return {}
	return data
