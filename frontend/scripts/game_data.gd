extends Node
## 地图数据 + 寻路网格 + 房间查询 (autoload: Game)
## 数据源: assets/map/jishilou_floor3/floor3_grid.json + rooms.json
## (由 tools/extract_map_data.py 从绘图工具导出的场景自动提取)

const MAP_DIR := "res://assets/map/jishilou_floor3"

var tile_size: int = 16
var origin := Vector2i.ZERO          # 地图原点格坐标
var size := Vector2i.ZERO            # 网格宽高(格)
var astar := AStarGrid2D.new()
var rooms := {}                      # "R01" -> 元数据字典
var region_cells := {}               # "R01" -> Array[Vector2i]
var room_grid := []                  # 房间号矩阵 (int)
var _num_to_id := {}                 # 12 -> "R12"

func _ready() -> void:
	var grid: Dictionary = _load_json(MAP_DIR + "/floor3_grid.json")
	origin = Vector2i(grid.origin[0], grid.origin[1])
	size = Vector2i(grid.size[0], grid.size[1])
	tile_size = int(grid.tile_size)
	room_grid = grid.room_grid
	var rooms_data: Dictionary = _load_json(MAP_DIR + "/rooms.json")
	for r in rooms_data.get("regions", []):
		rooms[r.id] = r
		_num_to_id[int(r.id.substr(1))] = r.id
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
	for y in size.y:
		for x in size.x:
			var n: int = room_grid[y][x]
			if n > 0 and _num_to_id.has(n):
				var id: String = _num_to_id[n]
				if not region_cells.has(id):
					region_cells[id] = []
				region_cells[id].append(Vector2i(x, y))
	print("[Game] 网格 %dx%d 房间 %d 个" % [size.x, size.y, rooms.size()])

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
	var c := cell_of(world)
	if c.x < 0 or c.y < 0 or c.x >= size.x or c.y >= size.y:
		return ""
	return _num_to_id.get(room_grid[c.y][c.x], "")

func room_name(rid: String) -> String:
	return rooms.get(rid, {}).get("name", rid)

func room_type(rid: String) -> String:
	return rooms.get(rid, {}).get("type", "room")

func nav_world_of(rid: String) -> Vector2:
	var nav: Array = rooms.get(rid, {}).get("nav_point", [0, 0])
	return world_of(Vector2i(nav[0], nav[1]))

func random_world_in(rid: String) -> Vector2:
	var cells: Array = region_cells.get(rid, [])
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
