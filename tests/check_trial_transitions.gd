extends SceneTree
## 走真实地图浏览器的交互入口，核对跨馆和原有楼内楼梯共存。

func _initialize() -> void:
	call_deferred("verify")

func check(ok: bool, label: String) -> bool:
	if not ok:
		push_error("FAIL: " + label)
		quit(1)
	return ok

func stair(viewer, id: String) -> Dictionary:
	for item: Dictionary in viewer.manifest.objects:
		if item.id == id:
			return item
	return {}

func enter(viewer, tile: Vector2i, from: Vector2i, key: Key) -> void:
	viewer.walk_cell = tile + from
	var event := InputEventKey.new()
	event.keycode = key
	event.pressed = true
	root.push_input(event)
	viewer._process(0.2)
	event.pressed = false
	root.push_input(event)

func verify() -> void:
	root.get_node("ApiClient").set_process(false)
	var game := root.get_node("Game")
	var viewer = load("res://scenes/map_viewer.tscn").instantiate()
	root.add_child(viewer)
	viewer.open()
	viewer.bar.get_node("Walk").button_pressed = true
	var j3_entry: Vector2i = game.stair_entry(game.FLOOR3, "stair_west")
	var zx_entry: Vector2i = game.stair_entry(game.ZHIXING, "zx2_stair_w")
	var entries: Dictionary = game.floor_data[game.FLOOR3].walk_stairs
	var campus: Vector2i = entries.keys().filter(func(cell: Vector2i) -> bool: return entries[cell].map_id == game.ZHIXING)[0]
	if not check(campus != j3_entry and viewer.walk_starts.has(campus), "west campus passage has a separate reachable entry tile"): return
	enter(viewer, campus, Vector2i.RIGHT, KEY_LEFT)
	if not check(viewer.current_floor == 2 and viewer.walk_cell == zx_entry and viewer.can_walk(zx_entry) and viewer.walker.visible, "Jishi floor3 west to Zhixin floor2 west lands on walkable stair entry"): return
	viewer._process(0.2)
	if not check(viewer.current_floor == 2, "standing on the arrival tile does not bounce back"): return
	enter(viewer, zx_entry, Vector2i.RIGHT, KEY_LEFT)
	if not check(viewer.current_floor == 0 and viewer.walk_cell == j3_entry and viewer.can_walk(j3_entry), "Zhixin west returns to Jishi floor3 west"): return
	enter(viewer, j3_entry, Vector2i.LEFT, KEY_RIGHT)
	if not check(viewer.current_floor == 1, "original west stair still reaches floor4"): return
	enter(viewer, game.stair_entry(game.FLOOR4, "stair_west"), Vector2i.LEFT, KEY_RIGHT)
	if not check(viewer.current_floor == 0, "original west stair still returns to floor3"): return
	enter(viewer, game.stair_entry(game.FLOOR3, "stair_east"), Vector2i.LEFT, KEY_RIGHT)
	if not check(viewer.current_floor == 1, "original east stair still reaches floor4"): return
	enter(viewer, game.stair_entry(game.FLOOR4, "stair_east"), Vector2i.LEFT, KEY_RIGHT)
	if not check(viewer.current_floor == 0, "original east stair still returns to floor3"): return
	viewer.show_floor(2)
	viewer._use(stair(viewer, "zx2_stair_ne"))
	if not check(viewer.current_floor == 2, "unmapped stairs remain informational"): return
	viewer.close()
	if not check(not paused, "closing trial restores previous pause state"): return
	print("PASS: trial west campus passage both directions, walkable landings, original west/east floor3-floor4 stairs")
	quit(0)
