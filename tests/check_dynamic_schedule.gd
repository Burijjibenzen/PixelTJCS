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
	var social_ui
	for child in main.get_children():
		if child is CanvasLayer and child.get_script():
			child.set_process(false)
			child.visible = false
			if child.get_script().resource_path.ends_with("social_ui.gd"):
				social_ui = child
				child.profile_panel.set_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
	var subject = main.npcs.filter(func(actor): return actor.npc_name == "自嘲熊").front()
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
	# 站定后强制重报相同坐标，不能被位置去重缓存永久跳过。
	subject.set_physics_process(false)
	var drain_deadline := Time.get_ticks_msec() + 5000
	while not api._sending_position.is_empty() and Time.get_ticks_msec() < drain_deadline:
		await process_frame
	api.report_arrival(subject, true)
	var packet: Dictionary = api._sending_position if api._sending_position.get("npc", "") == subject.npc_name else api._pending_positions.get(subject.npc_name, {})
	var local_position: Vector2 = subject.global_position - root.get_node("Game").map_offset(subject.map_id)
	if packet.is_empty() or not packet.has("x") or not packet.has("y") or Vector2(packet.x, packet.y).distance_to(local_position) > 0.2:
		fail("Position heartbeat must refresh stationary coordinates in map-local pixels");return
	if main.get_node_or_null("UI/SchedulePanel") != null:
		fail("The duplicate fixed schedule panel must be removed");return
	var profile = social_ui.profile_panel
	profile.cache[subject.npc_name] = await fetch("/npcs/" + subject.npc_name.uri_encode() + "/profile")
	for index in social_ui.picker.item_count:
		if social_ui.picker.get_item_text(index) == subject.npc_name:
			social_ui.picker.select(index)
			social_ui.picker.item_selected.emit(index)
	social_ui.visible = true
	social_ui.panel.visible = true
	social_ui.tabs.current_tab = 5
	profile._render_location()
	if not "活动状态：已到达／活动中" in profile.activity_label.text or not "安排依据：" in profile.activity_label.text or not "行程说明：" in profile.activity_label.text or not "图书分馆" in profile.destination_label.text:
		fail("Profile activity fields need clear labels and live status/destination/reason");return
	await process_frame
	await process_frame
	var scroll: ScrollContainer = profile.get_child(2)
	if scroll.get_child(0).size.x > scroll.size.x + 1:
		fail("Profile activity fields must fit the panel width");return
	var preview := OS.get_environment("ROAM_DYNAMIC_PREVIEW")
	if not preview.is_empty():
		main._select_floor(2)
		api.display_minutes = float(world.minutes)
		api._refresh_clock_text()
		main._process(0.0)
		await process_frame
		await RenderingServer.frame_post_draw
		if root.get_texture().get_image().save_png(preview) != OK:
			fail("Could not save activity panel preview");return
	print("PASS: dynamic event -> real cross-building navigation -> active dwell; stable target across midnight; labelled profile activity fields; no duplicate fixed panel")
	quit(0)
