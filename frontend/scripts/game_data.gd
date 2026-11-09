extends Node
## 地板图层给出房间归属，各楼层的 A* 网格负责通行。

const FLOOR3 := "jishilou_floor3"
const FLOOR4 := "jishilou_floor4"
const ZHIXING := "zhixinguan_floor2"
const MAP_IDS := [FLOOR3, FLOOR4, ZHIXING]

var tile_size := 16
var floor_data := {}
var size := Vector2i.ZERO  # 地图浏览器以三楼的大小确定默认缩放。
var _links := {}

# 旧的三楼调试和日程调用方仍可读取原来的房间数据。
var rooms: Dictionary:
	get: return floor_data[FLOOR3].rooms
var waypoint_rooms: Dictionary:
	get: return floor_data[FLOOR3].waypoint_rooms
var floor_cells: Dictionary:
	get: return floor_data[FLOOR3].floor_cells
var door_cells: Array:
	get: return floor_data[FLOOR3].door_cells

func _ready() -> void:
	var next_offset := Vector2.ZERO
	for map_id: String in MAP_IDS:
		var dir := "res://assets/map/%s/" % map_id
		var grid := _load_json(dir + "grid.json")
		var manifest := _load_json(dir + "manifest.json")
		var astar := AStarGrid2D.new()
		astar.region = Rect2i(Vector2i.ZERO, _cell(grid.size))
		astar.cell_size = Vector2.ONE * tile_size
		astar.diagonal_mode = AStarGrid2D.DIAGONAL_MODE_NEVER
		astar.default_compute_heuristic = AStarGrid2D.HEURISTIC_MANHATTAN
		astar.default_estimate_heuristic = AStarGrid2D.HEURISTIC_MANHATTAN
		astar.update()
		for y in astar.region.size.y:
			for x in astar.region.size.x:
				if grid.obstacle[y][x] == "1":
					astar.set_point_solid(Vector2i(x, y))
		var floor := {
			"name": manifest.name, "origin": _cell(grid.origin), "size": astar.region.size,
			"offset": next_offset, "astar": astar, "rooms": {}, "waypoint_rooms": {},
			"room_by_cell": {}, "floor_cells": {}, "region_cells": {}, "door_cells": [],
			"stairs": {}, "doors": [],
		}
		for room: Dictionary in manifest.rooms:
			floor.rooms[room.id] = room
			for waypoint in room.get("waypoints", {}):
				floor.waypoint_rooms[waypoint] = room.id
		for obj: Dictionary in manifest.objects:
			if obj.type == "door":
				floor.doors.append(obj)
				for point in obj.get("passage_cells", obj.get("cells", [obj.cell])):
					floor.door_cells.append(_cell(point))
			elif obj.type == "stair":
				floor.stairs[obj.id] = obj
		floor_data[map_id] = floor
		_links[map_id] = []
		next_offset.x += (astar.region.size.x + 64) * tile_size
	size = floor_data[FLOOR3].size
	_connect_floors()

func _connect_floors() -> void:
	for map_id: String in MAP_IDS:
		for stair: Dictionary in floor_data[map_id].stairs.values():
			var dest: String = stair.get("destination_map", "")
			if not floor_data.has(dest):
				continue
			# 入口取对向楼梯记录的落地格，避免走进墙上的楼梯画面。
			var reverse: Dictionary = floor_data[dest].stairs.get(stair.id, {})
			if reverse.get("destination_map", "") == map_id:
				_add_link(map_id, _cell(reverse.destination_cell), dest, _cell(stair.destination_cell))
	# 跨馆时在两栋楼的西侧楼梯口转场，省略未绘制的室外路程。
	var west_jishi := _cell(floor_data[FLOOR4].stairs.stair_west.destination_cell)
	var west_zhixing := _cell(floor_data[ZHIXING].stairs.zx2_stair_w.cell)
	_add_link(FLOOR3, west_jishi, ZHIXING, west_zhixing)
	_add_link(ZHIXING, west_zhixing, FLOOR3, west_jishi)

func _add_link(map_id: String, start: Vector2i, dest: String, end: Vector2i) -> void:
	if is_blocked(start, map_id) or is_blocked(end, dest):
		push_error("楼层连接的落脚点不可通行：%s %s → %s %s" % [map_id, start, dest, end])
		return
	_links[map_id].append({"at": world_of(start, map_id), "dest": dest, "landing": world_of(end, dest)})

func _cell(raw: Array) -> Vector2i:
	return Vector2i(int(raw[0]), int(raw[1]))

func map_offset(map_id: String) -> Vector2:
	return floor_data[map_id].offset

func map_name(map_id: String) -> String:
	return floor_data[map_id].name

func bind_room_layers(map_node: Node2D, map_id: String = FLOOR3) -> void:
	var floor: Dictionary = floor_data[map_id]
	floor.room_by_cell.clear()
	for room: Dictionary in floor.rooms.values():
		var layer := map_node.get_node(NodePath(room.layer)) as TileMapLayer
		var cells := layer.get_used_cells()
		var walkable: Array[Vector2i] = []
		for cell in cells:
			floor.room_by_cell[cell] = room.id
			if not is_blocked(cell, map_id):
				walkable.append(cell)
		floor.floor_cells[room.id] = cells
		floor.region_cells[room.id] = walkable

func is_blocked(cell: Vector2i, map_id: String = FLOOR3) -> bool:
	var astar: AStarGrid2D = floor_data[map_id].astar
	return not astar.is_in_boundsv(cell) or astar.is_point_solid(cell)

func cell_of(world: Vector2, map_id: String = FLOOR3) -> Vector2i:
	return Vector2i(((world - map_offset(map_id)) / tile_size).floor()) - Vector2i(floor_data[map_id].origin)

func world_corner(cell: Vector2i, map_id: String = FLOOR3) -> Vector2:
	return map_offset(map_id) + Vector2(cell + Vector2i(floor_data[map_id].origin)) * tile_size

func world_of(cell: Vector2i, map_id: String = FLOOR3) -> Vector2:
	return world_corner(cell, map_id) + Vector2.ONE * tile_size * 0.5

func path_between(from_world: Vector2, to_world: Vector2, map_id: String = FLOOR3) -> PackedVector2Array:
	var a := cell_of(from_world, map_id)
	var b := cell_of(to_world, map_id)
	var result := PackedVector2Array()
	if is_blocked(a, map_id) or is_blocked(b, map_id):
		return result
	var astar: AStarGrid2D = floor_data[map_id].astar
	for cell in astar.get_id_path(a, b):
		result.append(world_of(cell, map_id))
	return result

# 各条楼梯的代价取实际的楼内步数，因此东侧角色可以选更近的东楼梯。
func route(map_id: String, start: Vector2, dest: String, target: Vector2, visited: Array = []) -> Array:
	if map_id == dest:
		var path := path_between(start, target, map_id)
		return [] if path.is_empty() else [{"path": path}]
	var best: Array = []
	var best_length := INF
	for link: Dictionary in _links[map_id]:
		if visited.has(link.dest):
			continue
		var walk := path_between(start, link.at, map_id)
		if walk.is_empty():
			continue
		var rest := route(link.dest, link.landing, dest, target, visited + [map_id])
		if rest.is_empty():
			continue
		var length := walk.size()
		for section: Dictionary in rest:
			length += section.path.size()
		if length < best_length:
			best_length = length
			best = [{"path": walk, "dest": link.dest, "landing": link.landing}] + rest
	return best

func room_id_at(world: Vector2, map_id: String = FLOOR3) -> String:
	return floor_data[map_id].room_by_cell.get(cell_of(world, map_id), "")

func actual_room(map_id: String, rid: String) -> String:
	return floor_data[map_id].waypoint_rooms.get(rid, rid)

func has_room(map_id: String, rid: String) -> bool:
	return floor_data.has(map_id) and floor_data[map_id].rooms.has(actual_room(map_id, rid))

func room_name(rid: String, map_id: String = FLOOR3) -> String:
	return floor_data[map_id].rooms.get(actual_room(map_id, rid), {}).get("name", rid)

func room_type(rid: String, map_id: String = FLOOR3) -> String:
	return floor_data[map_id].rooms.get(actual_room(map_id, rid), {}).get("type", "room")

func nav_world_of(rid: String, map_id: String = FLOOR3) -> Vector2:
	var room: Dictionary = floor_data[map_id].rooms[actual_room(map_id, rid)]
	return world_of(_cell(room.get("waypoints", {}).get(rid, room.spawn)), map_id)

func random_world_in(rid: String, map_id: String = FLOOR3) -> Vector2:
	var cells: Array = floor_data[map_id].region_cells[actual_room(map_id, rid)]
	return world_of(cells.pick_random(), map_id)

func all_room_ids(map_id: String = FLOOR3) -> Array:
	return floor_data[map_id].rooms.keys()

func _load_json(path: String) -> Dictionary:
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not data is Dictionary:
		push_error("无法读取地图数据：" + path)
		return {}
	return data
