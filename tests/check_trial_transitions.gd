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

func verify() -> void:
	root.get_node("ApiClient").set_process(false)
	var game := root.get_node("Game")
	var viewer = load("res://scenes/map_viewer.tscn").instantiate()
	root.add_child(viewer)
	viewer.open()
	viewer.bar.get_node("Walk").button_pressed = true
	var j3_entry: Vector2i = game.stair_entry(game.FLOOR3, "stair_west")
	var zx_entry: Vector2i = game.stair_entry(game.ZHIXING, "zx2_stair_w")
	viewer.show_floor(0, j3_entry)
	viewer._use(stair(viewer, "stair_west"))
	if not check(viewer.stair_options.size() == 2 and viewer.stair_menu.visible, "three-floor west stair offers upstairs and campus passage"): return
	var campus_index := -1
	for index in viewer.stair_options.size():
		if viewer.stair_options[index].map_id == game.ZHIXING:
			campus_index = index
	if not check(campus_index >= 0, "campus passage option exists"): return
	viewer.stair_menu.id_pressed.emit(campus_index)
	if not check(viewer.current_floor == 2 and viewer.walk_cell == zx_entry and viewer.can_walk(zx_entry) and viewer.walker.visible, "Jishi floor3 west to Zhixin floor2 west lands on walkable stair entry"): return
	viewer._use(stair(viewer, "zx2_stair_w"))
	if not check(viewer.current_floor == 0 and viewer.walk_cell == j3_entry and viewer.can_walk(j3_entry), "Zhixin west returns to Jishi floor3 west"): return
	viewer._use(stair(viewer, "stair_west"))
	viewer.stair_menu.id_pressed.emit(0)
	if not check(viewer.current_floor == 1, "original west stair still reaches floor4"): return
	viewer._use(stair(viewer, "stair_west"))
	if not check(viewer.current_floor == 0, "original west stair still returns to floor3"): return
	viewer._use(stair(viewer, "stair_east"))
	if not check(viewer.current_floor == 1, "original east stair still reaches floor4"): return
	viewer._use(stair(viewer, "stair_east"))
	if not check(viewer.current_floor == 0, "original east stair still returns to floor3"): return
	viewer.show_floor(2)
	viewer._use(stair(viewer, "zx2_stair_ne"))
	if not check(viewer.current_floor == 2, "unmapped stairs remain informational"): return
	viewer.close()
	if not check(not paused, "closing trial restores previous pause state"): return
	print("PASS: trial west campus passage both directions, walkable landings, original west/east floor3-floor4 stairs")
	quit(0)
