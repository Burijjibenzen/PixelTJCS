extends SceneTree
## 通过真实 ApiClient 接受动态目标、实际走过跨馆楼梯并上报完整到达。
var http: HTTPRequest
var backend: String

func _initialize() -> void:
	call_deferred("verify")

func fetch(path: String, post := false) -> Dictionary:
	if http.request(backend + path, [], HTTPClient.METHOD_POST if post else HTTPClient.METHOD_GET) != OK:
		return {}
	var result: Array = await http.request_completed
	if result[0] != HTTPRequest.RESULT_SUCCESS or result[1] != 200:
		print("HTTP test failure ", path, " result=", result[0], " status=", result[1], " body=", result[3].get_string_from_utf8())
		return {}
	var data = JSON.parse_string(result[3].get_string_from_utf8())
	return data if data is Dictionary else {}

func fail(message: String) -> void:
	push_error(message)
	quit(1)

func verify() -> void:
	var api := root.get_node("ApiClient")
	api.set_process(false)
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	backend = OS.get_environment("ROAM_DYNAMIC_BACKEND")
	api.backend_url = backend
	http = HTTPRequest.new()
	# --fixed-fps 加速物理帧，HTTPRequest 的引擎计时器也会变快；由外层90秒看门狗限时。
	http.timeout = 0.0
	api._http_post.timeout = 0.0
	var probe := {"npc": "report-probe", "activity_id": "new-activity"}
	api._sending_position = probe
	api._on_report_done(HTTPRequest.RESULT_SUCCESS, 200, PackedStringArray(), '{"detail":"busy"}'.to_utf8_buffer())
	if api._confirmed_positions.has("report-probe"):
		fail("Ignored busy arrival must be retried after release");return
	api._sending_position = probe
	api._on_report_done(HTTPRequest.RESULT_SUCCESS, 200, PackedStringArray(), '{"detail":"already"}'.to_utf8_buffer())
	if api._confirmed_positions.get("report-probe", {}).get("activity_id", "") != "new-activity":
		fail("Confirmed arrival must retain its activity identity");return
	root.add_child(http)
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	for child in main.get_children():
		if child is CanvasLayer and child.get_script():
			child.set_process(false)
			child.visible = false
			if child.get_script().resource_path.ends_with("social_ui.gd"):
				child.profile_panel.set_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
	var subject = main.npcs.filter(func(actor): return actor.npc_name == "周冲").front()
	subject.set_physics_process(true)
	var visited: Array[String] = []
	subject.floor_changed.connect(func(actor): visited.append(actor.map_id))
	var world := await fetch("/world")
	if world.is_empty():
		fail("Initial dynamic world request failed");return
	api._apply_world(world)
	var identity: String = world.npcs[subject.npc_name].activity.id
	if subject.goal_map_id != "zhixinguan_floor2" or subject.goal_room != "zx2_lib" or not subject.is_travelling():
		fail("Dynamic plan did not enter the real NPC navigation route");return
	# 测试端点模拟已跨过基础作息时段甚至午夜；生产快进仍走连续时钟。
	world = await fetch("/test/dynamic/advance", true)
	if not world.has("npcs"):
		fail("Test clock boundary request failed");return
	api._apply_world(world)
	if world.npcs[subject.npc_name].activity.id != identity or subject.goal_room != "zx2_lib":
		fail("In-flight target changed after daily schedule boundary");return
	var success := false
	for step in range(18000):
		await physics_frame
		if step % 120 != 0 and subject.is_travelling():
			continue
		api.report_arrival(subject)
		world = await fetch("/world")
		if world.is_empty():
			continue
		api._apply_world(world)
		main._refresh_schedule_ui()
		var activity: Dictionary = world.npcs[subject.npc_name].activity
		if step % 1200 == 0:
			print("Navigation frame ", step, " at ", subject.map_id, " goal ", subject.goal_room, " state ", activity.state)
		if activity.id != identity:
			fail("Plan cancelled before real destination arrival");return
		if activity.state == "active":
			var game := root.get_node("Game")
			if subject.is_travelling() or subject.map_id != "zhixinguan_floor2" or game.room_id_at(subject.global_position, subject.map_id) != "zx2_lib":
				fail("Activity started before physical destination arrival");return
			if not "zhixinguan_floor2" in visited:
				fail("Cross-building stair route was not observed");return
			success = true
			break
	if not success:
		fail("Dynamic library activity never actually arrived");return
	var label: Label = main.get_node("UI/SchedulePanel/Scroll/ScheduleLabel")
	if not "已到达／活动中" in label.text or not "原因：" in label.text or not "图书分馆" in label.text:
		fail("Schedule panel is missing the activity status/destination/reason");return
	await process_frame
	await process_frame
	var scroll: ScrollContainer = label.get_parent()
	if scroll.get_v_scroll_bar().max_value <= scroll.size.y or label.size.x > scroll.size.x + 1:
		fail("Schedule list must fit its width and scroll for all ten NPCs");return
	var preview := OS.get_environment("ROAM_DYNAMIC_PREVIEW")
	if not preview.is_empty():
		main._select_floor(2)
		main._process(0.0)
		var preceding_lines := label.text.left(label.text.find("周冲")).count("\n")
		scroll.scroll_vertical = int(preceding_lines * label.get_line_height())
		await process_frame
		await RenderingServer.frame_post_draw
		if root.get_texture().get_image().save_png(preview) != OK:
			fail("Could not save activity panel preview");return
	print("PASS: dynamic event -> target sync -> real cross-building navigation -> arrival HTTP -> active dwell; stable target across midnight; scrollable schedule status/reasons")
	quit(0)
