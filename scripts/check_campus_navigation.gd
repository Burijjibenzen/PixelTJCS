extends SceneTree
## 用主场景的 NPC 和实际墙体复测跨层行走。命令见 docs/CODE_GUIDE.md。

var subject
var game
var main
var companion
var lecture_layer: TileMapLayer
var lecture_doors := {}  # 门洞格 -> 四楼讲座教室的对应门
var open_before_entry := {}
var crossed_door := {}
var arrived := false
var visited: Array[String] = []

func _initialize() -> void:
	call_deferred("run_checks")

func run_checks() -> void:
	root.get_node("ApiClient").set_process(false)
	game = root.get_node("Game")
	main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	# 导航回归只观察本机物理移动，不能连接开发中的真实后端改变目标或倍率。
	for child in main.get_children():
		if child is CanvasLayer:
			child.set_process(false)
	var api := root.get_node("ApiClient")
	api.backend_online = false
	api.simulation_paused = false
	var test_speed := OS.get_environment("ROAM_TEST_SPEED")
	api.simulation_speed = api.BASE_SPEED * (1.0 if test_speed.is_empty() else float(test_speed))
	for actor in main.npcs:
		actor.set_physics_process(false)
	subject = main.npcs.filter(func(actor): return actor.npc_id == "zhou_chong").front()
	companion = main.npcs.front()
	subject.set_physics_process(true)
	subject.arrived.connect(func(_actor): arrived = true)
	subject.floor_changed.connect(func(actor): visited.append(actor.map_id))
	await physics_frame
	var j3 := "jishilou_floor3"
	var j4 := "jishilou_floor4"
	var zx2 := "zhixinguan_floor2"
	for map_id in [j3, j4, zx2]:
		var rooms: Dictionary = game.floor_data[map_id].rooms
		var entrance: Vector2 = game.nav_world_of(str(rooms.keys()[0]), map_id)
		for room in rooms:
			var destination: Vector2 = game.nav_world_of(room, map_id)
			if entrance != destination and game.path_between(entrance, destination, map_id).is_empty():
				push_error("房间出生点不可达：%s/%s" % [map_id, room])
				quit(1)
				return
	if subject.map_id != j3:
		push_error("回归场景需要周冲从济事楼三楼出发")
		quit(1)
		return
	lecture_layer = main.get_node("Maps/jishilou_floor4/door")
	var manifest: Dictionary = game._load_json("res://assets/map/jishilou_floor4/manifest.json")
	for door: Dictionary in manifest.objects:
		if door.type == "door" and door.room == "R05":
			var art := {}
			for raw in door.art_cells:
				var cell := Vector2i(raw[0], raw[1])
				if lecture_layer.get_cell_source_id(cell) >= 0:
					art[cell] = lecture_layer.get_cell_atlas_coords(cell)
			for raw in door.passage_cells:
				lecture_doors[Vector2i(raw[0], raw[1])] = art
	if not await walk_to(j4, "R05", [j4], true) or not await verify_doors(j4):
		quit(1)
		return
	if not await walk_to(zx2, "zx2_lib", [j3, zx2]):
		quit(1)
		return
	if not await walk_to(j3, "R05", [j3]):
		quit(1)
		return
	print("PASS: 40 个房间落点、两馆间往返、同编号房间的楼层归属及到达信号")
	quit(0)

func walk_to(map_id: String, room_id: String, expected_floors: Array, check_lecture_entry := false) -> bool:
	visited.clear()
	arrived = false
	if not subject.go_to_location(map_id, room_id):
		push_error("无法规划 %s/%s" % [map_id, room_id])
		return false
	for step in range(18000):
		await physics_frame
		if check_lecture_entry and subject.map_id == map_id:
			var foot: Vector2i = game.cell_of(subject.global_position, map_id)
			if lecture_doors.has(foot):
				if crossed_door.is_empty():
					crossed_door = lecture_doors[foot]
					if not open_before_entry.has(foot):
						push_error("到达门洞前一格时，门扇尚未打开")
						return false
					# 第二名角色停在同一门洞；周冲先通过，门应继续保持打开。
					companion.map_id = map_id
					companion.global_position = subject.global_position
				if not door_matches(lecture_layer, true):
					push_error("周冲穿过四楼讲座教室门洞时，门扇仍有可见图块")
					return false
			elif crossed_door.is_empty():
				for direction: Vector2i in [Vector2i.UP, Vector2i.DOWN, Vector2i.LEFT, Vector2i.RIGHT]:
					var adjacent := foot + direction
					if lecture_doors.has(adjacent) and door_matches(lecture_layer, true, lecture_doors[adjacent]):
						open_before_entry[adjacent] = true
		if arrived:
			var actual_room: String = game.room_id_at(subject.global_position, subject.map_id)
			if subject.map_id != map_id or actual_room != room_id or visited != expected_floors:
				push_error("到达位置或经过楼层错误：%s/%s 经 %s" % [subject.map_id, actual_room, visited])
				return false
			print("到达 %s/%s，经过 %s，物理帧 %d" % [map_id, room_id, visited, step])
			return true
	push_error("行走超时：目的地 %s/%s；实际 %s %s" % [map_id, room_id, subject.map_id, subject.global_position])
	return false

func door_matches(layer: TileMapLayer, opened: bool, tiles := crossed_door) -> bool:
	if tiles.is_empty():
		return false
	for cell: Vector2i in tiles:
		if opened:
			if layer.get_cell_source_id(cell) >= 0:
				return false
		elif layer.get_cell_atlas_coords(cell) != tiles[cell]:
			return false
	return true

func verify_doors(map_id: String) -> bool:
	if not door_matches(lecture_layer, true):
		push_error("未观察到周冲进门，或同伴仍在门洞时自动关门")
		return false
	var browser = main.get_node("MapBrowser")
	browser.open()
	browser.show_floor(1)
	var preview := browser.map_node.get_node("door") as TileMapLayer
	var interior: Vector2i = game.cell_of(companion.global_position, map_id)
	var independent: bool = door_matches(preview, false) and not browser.can_walk(interior)
	browser._select_cell(interior)
	independent = independent and door_matches(preview, true) and browser.can_walk(interior)
	browser._select_cell(interior)
	independent = independent and door_matches(preview, false) and door_matches(lecture_layer, true)
	browser.close()
	if not independent:
		push_error("手动试走的门状态和 NPC 主场景互相影响")
		return false
	companion.map_id = companion.home_map_id
	companion.global_position = game.nav_world_of(companion.home_room, companion.home_map_id)
	for tick in range(120):
		await physics_frame
		if door_matches(lecture_layer, false):
			print("门扇验证通过：进门、两人先后通过、试走状态独立、离开后还原图块")
			return true
	push_error("最后一名角色离开后未还原教室门扇")
	return false
