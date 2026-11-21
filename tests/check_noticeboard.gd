extends SceneTree
var http: HTTPRequest
var backend: String

func _initialize() -> void:
	call_deferred("verify")

func fail(message: String) -> void:
	push_error(message)
	quit(1)

func fetch(path: String) -> Dictionary:
	if http.request(backend + path) != OK:
		return {}
	var response: Array = await http.request_completed
	if response[0] != HTTPRequest.RESULT_SUCCESS or response[1] != 200:
		return {}
	var data = JSON.parse_string(response[3].get_string_from_utf8())
	return data if data is Dictionary else {}

func wait_panel(panel) -> bool:
	var until := Time.get_ticks_msec() + 10000
	while panel.pending and Time.get_ticks_msec() < until:
		await process_frame
	return not panel.pending

func click(point: Vector2) -> void:
	for pressed in [true, false]:
		var event := InputEventMouseButton.new()
		event.position = point
		event.global_position = point
		event.button_index = MOUSE_BUTTON_LEFT
		event.pressed = pressed
		root.push_input(event, true)

func text_in(node: Node) -> String:
	var text := str(node.text) if node is Label else ""
	for child in node.get_children():
		text += text_in(child)
	return text

func type_text(control: Control, text: String) -> void:
	control.grab_focus()
	for character in text:
		var event := InputEventKey.new()
		event.pressed = true
		event.unicode = character.unicode_at(0)
		root.push_input(event, true)

func walk_to_point(subject, destination: Vector2) -> bool:
	if not subject.go_to(destination):
		return false
	for step in range(20000):
		await physics_frame
		if not subject.is_travelling():
			return subject.global_position.distance_to(destination) < 3.0
	return false

func verify() -> void:
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	var api := root.get_node("ApiClient")
	api.set_process(false)
	backend = OS.get_environment("ROAM_NOTICE_BACKEND")
	api.backend_url = backend
	api._http_post.timeout = 0
	http = HTTPRequest.new()
	http.timeout = 0
	root.add_child(http)
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	main.social_ui.set_process(false)
	main.social_ui.profile_panel.set_process(false)
	main.social_ui.panel.hide()
	for actor in main.npcs:
		actor.set_physics_process(false)
	var subject = main.npcs.filter(func(actor): return actor.npc_name == "陈卷").front()
	var panel = main.notice_ui
	panel.set_process(false)
	panel.backend_url = backend
	panel.http.timeout = 0
	# 三张地图均通过实际墙体走到板前；预检阶段尚未发布公告。
	var game := root.get_node("Game")
	api.simulation_speed = api.BASE_SPEED * 6
	subject.set_physics_process(true)
	for map_id in game.MAP_IDS:
		var notice: Dictionary = game.floor_data[map_id].noticeboards[0]
		if not subject.go_to_location(map_id, notice.room_id):
			fail("No route to notice board area: " + map_id);return
		for step in range(30000):
			await physics_frame
			if not subject.is_travelling():
				break
		if subject.map_id != map_id or subject.is_travelling():
			fail("NPC could not physically reach the notice board map");return
		var cell := Vector2i(50, 106) if map_id == game.ZHIXING else Vector2i(18, 30)
		if not await walk_to_point(subject, game.world_of(cell, map_id)):
			fail("Actual navigation cannot reach the board front: " + map_id);return
	if not subject.go_to_location(game.FLOOR3, "R01"):
		fail("Could not return to the first board corridor");return
	for step in range(30000):
		await physics_frame
		if not subject.is_travelling():
			break
	if not await walk_to_point(subject, game.world_of(Vector2i(29, 28), game.FLOOR3)):
		fail("Could not walk to the nearby discovery position");return
	subject.set_physics_process(false)
	api.simulation_speed = api.BASE_SPEED
	api.last_world = await fetch("/world")
	api.backend_online = true
	api._npcs = [subject]
	# 精确落点快照重复到达不能重新起步；撤去落点后，同一区域也要恢复房间目标。
	var point_info := {"target_map_id": game.FLOOR3, "target_room_id": "R01", "target_position": {"x": 296, "y": 488}}
	api._apply_world({"npcs": {subject.npc_name: point_info}})
	var first_sections: Array = subject._sections
	api._apply_world({"npcs": {subject.npc_name: point_info}})
	if not is_same(first_sections, subject._sections):
		fail("Repeated point snapshots restarted the same walk");return
	api._apply_world({"npcs": {subject.npc_name: {"target_map_id": game.FLOOR3, "target_room_id": "R01"}}})
	if api._point_targets.has(subject.npc_name) or subject.path[-1].distance_to(game.nav_world_of("R01", game.FLOOR3)) > 1:
		fail("Removing a point target did not restore the same-room destination");return
	# 顶部入口始终各占一个布局单元，弹窗会让出给新打开的功能。
	await process_frame
	var previous := Rect2()
	for button: Control in main.get_node("UI/ModeBar").get_children():
		var rect := button.get_global_rect()
		if rect.intersects(previous) or rect.intersects(main.get_node("UI/BtnAdv1h").get_global_rect()):
			fail("Mode buttons must not overlap each other or time controls");return
		previous = rect
	var left_bar := previous
	for control_name in ["BtnAdv1h", "BtnAdv4h", "BtnAdv3d", "SpeedPicker", "BtnPause"]:
		var right: Rect2 = main.get_node("UI/" + control_name).get_global_rect()
		if right.position.y != left_bar.position.y or right.size.y != left_bar.size.y:
			fail("Time controls must align with the mode buttons");return
		if right.position.x - previous.end.x < 4.0:
			fail("Time controls need separate click targets");return
		previous = right
	var clock: Rect2 = main.get_node("UI/ClockLabel").get_global_rect()
	var camera_label: Rect2 = main.get_node("UI/InfoLabel").get_global_rect()
	if clock.position.y != camera_label.position.y or clock.size.y != camera_label.size.y:
		fail("Clock and camera labels must align");return
	main.player_ui.mode_button.pressed.emit()
	if not main.player_ui.window.visible:
		fail("Player mode button is inaccessible");return
	main.get_node("UI/ModeBar/GodMode").pressed.emit()
	if main.player_ui.window.visible:
		fail("Player mode window still covers the god mode window");return
	if not panel.visible or not panel.god_mode or not await wait_panel(panel):
		fail("Top notice button did not open the current map board");return
	if panel.cards_scroll.get_index() >= panel.admin_controls.get_index():
		fail("Notice history must appear above the notice input");return
	if panel.whisper_cards.get_parent().get_index() >= panel.whisper_edit.get_index():
		fail("Whisper history must appear above the whisper input");return
	main._process(0.0)
	var clock_text: String = main.get_node("UI/ClockLabel").text
	if clock_text.find("后端驱动") < 0 or clock_text.find("对话事件流") <= clock_text.find("后端驱动") or clock_text.find("模型") <= clock_text.find("对话事件流"):
		fail("The event stream and model statuses must follow the backend indicator");return
	var board_id: String = panel.board_id
	if not board_id.begins_with("jishilou_floor3:"):
		fail("Notice button did not choose the current floor");return
	# 实际键盘输入经过多个自动刷新周期，必须保持焦点、光标及草稿。
	type_text(panel.content_edit, "正文草稿")
	var caret: int = panel.content_edit.get_caret_column()
	for cycle in range(3):
		panel._process(3.1)
	if panel.pending or panel.content_edit.text != "正文草稿" or panel.content_edit.get_caret_column() != caret or not panel.content_edit.has_focus():
		fail("Automatic refresh interrupted body input or caret position");return
	panel.content_edit.release_focus()
	panel._process(4.0)
	if panel.pending or panel.content_edit.text != "正文草稿":
		fail("Unfocused drafts must also pause automatic refresh");return
	var existing_card = panel.cards.get_child(0)
	panel.refresh()
	if not panel.content_edit.editable:
		fail("In-flight list requests must not lock the input controls");return
	type_text(panel.content_edit, "继续输入")
	if not await wait_panel(panel) or panel.content_edit.text != "正文草稿继续输入":
		fail("Typing during a list refresh must survive its response");return
	if panel.cards.get_child(0) != existing_card:
		fail("Unchanged refresh should preserve the current notice list");return
	panel.content_edit.text = "今天图书分馆开放，欢迎查阅研究资料。[b]此处应原样显示"
	panel.publish_button.pressed.emit()
	if not await wait_panel(panel):
		fail("Publishing through the real notice UI failed");return
	if not panel.content_edit.text.is_empty():
		fail("Successful publishing should clear the unchanged submitted draft");return
	# 模拟发布回包到达时已输入下一版内容；旧回包不得清空新草稿。
	panel.operation = "publish"
	panel.request_board = panel.board_id
	panel.request_draft = {"content": "旧正文"}
	panel.content_edit.text = "新正文草稿"
	panel._done(HTTPRequest.RESULT_SUCCESS, 200, PackedStringArray(), "{}".to_utf8_buffer())
	if panel.content_edit.text != "新正文草稿":
		fail("An old publish response cleared a newer draft");return
	panel.request_draft = {"content": "新正文草稿"}
	panel.content_edit.text += "\n"
	panel._done(HTTPRequest.RESULT_SUCCESS, 200, PackedStringArray(), "{}".to_utf8_buffer())
	if panel.content_edit.text != "新正文草稿\n":
		fail("Even a newly typed newline must survive an older publish response");return
	panel.content_edit.clear()
	panel.refresh()
	if not await wait_panel(panel) or not "今天图书分馆开放" in text_in(panel.cards) or not "发布于：第 1 天 08:00" in text_in(panel.cards):
		fail("Published content/time did not reach the notice cards");return
	var state := await fetch("/test/notice/status")
	if not state.get("reads", []).is_empty():
		fail("User viewing/publishing must not create NPC knowledge");return
	panel.hide()
	# 实际地图点击与试走物件入口都打开同一份公告。
	for map_id in game.MAP_IDS:
		main._show_floor(map_id, game.nav_world_of(str(game.all_room_ids(map_id)[0]), map_id))
		main.get_node("UI/ModeBar/GodMode").pressed.emit()
		if not panel.board_id.begins_with(map_id + ":") or not await wait_panel(panel):
			fail("Notice button must select a valid board on each of the three maps");return
		main.get_node("UI/ModeBar/GodMode").pressed.emit()
		if panel.visible:
			fail("Clicking the God mode button again must close it");return
	# 请求尚未返回时切换公告栏，旧响应不能覆盖当前选择。
	panel.open_board(str(panel.boards[0].id))
	panel.open_board(str(panel.boards[2].id))
	if not await wait_panel(panel) or panel.board_id != str(panel.boards[2].id):
		fail("Late notice response overwrote a newly selected board");return
	panel.hide()
	var board: Dictionary = game.floor_data[game.FLOOR3].noticeboards[0]
	var raw: Array = board.cells[0]
	var board_point: Vector2 = game.world_of(Vector2i(int(raw[0]), int(raw[1])), game.FLOOR3)
	var board_center: Vector2 = game.world_of(Vector2i(int(board.cell[0]), int(board.cell[1])), game.FLOOR3)
	main._show_floor(game.FLOOR3, board_point)
	await process_frame
	click(main.get_global_transform_with_canvas() * board_point)
	if not panel.visible or panel.admin_controls.visible or panel.board_id != board_id:
		fail("Clicking the board on the map did not open its notices");return
	if not await wait_panel(panel):
		fail("Map notice request timed out");return
	panel.hide()
	var browser = main.get_node("MapBrowser")
	browser.open()
	var item: Dictionary = browser.manifest.objects.filter(func(obj): return obj.type == "notice").front()
	browser._use(item)
	if not panel.visible or panel.admin_controls.visible or panel.board_id != board_id:
		fail("Map preview notice interaction did not open the same board");return
	if not await wait_panel(panel):
		fail("Preview notice request timed out");return
	browser._use(item)
	if panel.visible:
		fail("Interacting with the same notice board again must close it");return
	browser.close()
	# 位于发现范围、但尚在阅读范围之外，由后端下发具体地板落点。
	var saw_approach := false
	var saw_reading := false
	var completed := false
	var destination := Vector2.ZERO
	var until := Time.get_ticks_msec() + 20000
	while Time.get_ticks_msec() < until:
		api.report_arrival(subject, true)
		var world := await fetch("/world")
		var info: Dictionary = world.get("npcs", {}).get(subject.npc_name, {})
		if info.has("target_position"):
			destination = game.map_offset(subject.map_id) + Vector2(info.target_position.x, info.target_position.y)
			saw_approach = true
		api._apply_world(world)
		# 测试时后端冻结游戏时钟，仍让真实物理行走按原速进行。
		api.simulation_speed = api.BASE_SPEED
		state = await fetch("/test/notice/status")
		saw_reading = saw_reading or subject.npc_name in state.get("reading", [])
		if subject.npc_name in state.get("reading", []) and subject.global_position.distance_to(board_center) > 48:
			fail("Reading began before actually approaching the board");return
		if not state.get("comments", []).is_empty() and not saw_reading:
			fail("The reading bubble was emitted before arrival");return
		var reads: Array = state.get("reads", [])
		if reads.any(func(row): return row.npc == subject.npc_name and row.status == "completed"):
			completed = true
			break
		# 不让fixed-fps无上限跑帧把角色在一次HTTP往返中快跑出发现范围。
		# 收到真实目标后逐个物理步推进，仍由NPC原始碰撞和路径代码移动。
		if saw_approach:
			subject._physics_process(1.0 / 60.0)
		await physics_frame
	subject.set_physics_process(false)
	if not completed or not saw_reading or not saw_approach or destination == Vector2.ZERO:
		fail("Backend point target -> actual approach -> timed read did not complete");return
	var original: Dictionary = (await fetch("/world")).npcs[subject.npc_name]
	api._apply_world({"npcs": {subject.npc_name: original}})
	if api._point_targets.has(subject.npc_name) or subject.goal_room != original.target_room_id:
		fail("The original room target did not resume after reading");return
	if state.reads.size() != 1:
		fail("Reading created duplicate records or knowledge for other actors");return
	if state.get("comments", []).size() != 1 or not state.comments[0].text.begins_with("（自言自语）"):
		fail("Arrival should create one reading bubble linked to the event");return
	main.social_ui.show_profile(subject.npc_name)
	var timeline = main.social_ui.timeline_panel
	timeline._http.timeout = 0
	main.social_ui.tabs.current_tab = timeline.get_index()
	var timeline_until := Time.get_ticks_msec() + 5000
	while timeline.entries.get_child_count() == 0 and Time.get_ticks_msec() < timeline_until:
		await process_frame
	await process_frame
	await process_frame
	var first_card: PanelContainer = timeline.entries.get_child(0)
	var first_toggle: Button = first_card.get_node("Body/Summary/Toggle")
	var mouse := InputEventMouseMotion.new()
	mouse.position = first_toggle.get_global_rect().get_center()
	mouse.global_position = mouse.position
	root.push_input(mouse, true)
	timeline._update_hover()
	if first_card.get_theme_stylebox("panel") != timeline._card_hover:
		fail("Hovering an event must show its white outline");return
	click(mouse.position)
	if not first_toggle.button_pressed or not first_card.get_node("Body/Details").visible:
		fail("Clicking the summary card must expand its details");return
	var event_text := ""
	for card in timeline.entries.get_children():
		var summary: Button = card.get_node("Body/Summary/Toggle")
		summary.button_pressed = true
		event_text += card.get_node("Body/Summary/Inset/Fields").get_parsed_text() + card.get_node("Body/Details").get_parsed_text()
	if main.social_ui.tabs.current_tab != timeline.get_index() or not "阅读公告" in event_text \
		or not "已根据公告形成记忆" in event_text or not "[b]此处应原样显示" in event_text:
		fail("The existing profile must show the reader's stored notice text and memory result");return
	var other = main.npcs.filter(func(actor): return actor != subject).front()
	main.social_ui.show_profile(other.npc_name)
	main.social_ui.tabs.current_tab = timeline.get_index()
	timeline_until = Time.get_ticks_msec() + 5000
	while timeline._status.text != "该角色还没有事件记录" and Time.get_ticks_msec() < timeline_until:
		await process_frame
	if timeline.entries.get_child_count() != 0 or timeline._status.text != "该角色还没有事件记录":
		fail("Switching characters must show the selected actor's own timeline");return
	main.social_ui.panel.hide()
	panel.open_board(board_id)
	if not await wait_panel(panel):
		fail("Final refresh failed");return
	await process_frame
	await process_frame
	var window: PanelContainer = panel.get_node("Window")
	if not Rect2(Vector2.ZERO, root.get_visible_rect().size).encloses(window.get_global_rect()):
		fail("Notice form and reader controls must fit inside the viewport: %s in %s" % [window.get_global_rect(), root.get_visible_rect()]);return
	var preview := OS.get_environment("ROAM_NOTICE_PREVIEW")
	if not preview.is_empty():
		await process_frame
		await RenderingServer.frame_post_draw
		if root.get_texture().get_image().save_png(preview) != OK:
			fail("Notice preview could not be saved");return
	panel.open_board(board_id, true)
	if not await wait_panel(panel):
		fail("God mode did not switch back to the publishing panel");return
	await process_frame
	if not Rect2(Vector2.ZERO, root.get_visible_rect().size).encloses(window.get_global_rect()):
		fail("Both god mode controls must fit inside the viewport: %s in %s" % [window.get_global_rect(), root.get_visible_rect()]);return
	var remove: Button = panel.cards.get_child(0).get_child(0).get_child(1)
	remove.pressed.emit()
	if not panel.delete_confirm.visible:
		fail("Deleting a notice must ask for confirmation");return
	panel.delete_confirm.hide()
	if not "今天图书分馆开放" in text_in(panel.cards):
		fail("Closing the confirmation must leave the notice published");return
	remove.pressed.emit()
	panel.delete_confirm.confirmed.emit()
	panel.delete_confirm.hide()
	if not await wait_panel(panel) or "今天图书分馆开放" in text_in(panel.cards):
		fail("Confirming deletion must remove the notice from the list");return
	state = await fetch("/test/notice/status")
	var retained := await fetch("/npcs/" + str(subject.npc_name).uri_encode() + "/timeline")
	if state.reads.size() != 1 or not JSON.stringify(retained).contains("今天图书分馆开放"):
		fail("Deleting a notice must preserve the NPC reading record");return
	panel.tabs.current_tab = 1
	panel.refresh()
	panel.whisper_edit.text = "去西侧实验室"
	panel._update_buttons()
	if not panel.pending or panel.operation != "history" or panel.whisper_button.disabled:
		fail("A pending history fetch must leave a valid whisper available");return
	panel.whisper_button.pressed.emit()
	if not await wait_panel(panel) or not panel.status.text.begins_with("已指派") or not "去西侧实验室" in text_in(panel.whisper_cards):
		fail("God mode did not queue the whispered action and display its history");return
	api.backend_online = false
	panel.open_board(board_id)
	if not panel.publish_button.disabled or not "后端未连接" in panel.status.text:
		fail("Offline notice panel needs a clear, usable state");return
	api.backend_online = true
	api.apply_stream_world(await fetch("/world"))
	var old_world_id: String = api.world_id
	panel.open_board(board_id, true)
	if not await wait_panel(panel) or panel.reset_button.disabled:
		fail("Reset control is unavailable in god mode");return
	Engine.max_fps = 120
	var sidebar = main.character_status
	api._npcs = main.npcs
	api.apply_stream_world(await fetch("/world"))
	var actor_row = sidebar._rows.filter(func(row): return row.actor == subject).front()
	sidebar._set_expanded(false)
	panel.stage_button.pressed.emit()
	await process_frame
	await process_frame
	if not sidebar._panel.visible or not sidebar._stage_selection:
		fail("God mode must unfold the character sidebar for cast selection");return
	var actor_point: Vector2 = actor_row.get_global_rect().get_center()
	mouse.position = actor_point
	mouse.global_position = actor_point
	root.push_input(mouse, true)
	await process_frame
	if not actor_row.overlay.visible or actor_row.overlay_label.text != "移除":
		fail("The active role needs a Remove overlay");return
	click(actor_point)
	var stage_until := Time.get_ticks_msec() + 10000
	while (panel.stage_pending or not actor_row.offstage) and Time.get_ticks_msec() < stage_until:
		await process_frame
	if panel.stage_pending or subject.on_stage or subject.visible or not actor_row.offstage or sidebar._stage_selection:
		fail("Selecting the row must hide the NPC and gray out its row: " + panel.stage_status.text);return
	var saved_camera: Node2D = main.camera.target
	root.push_input(mouse, true)
	await process_frame
	if not actor_row.is_hovered() or main.camera.target != saved_camera or sidebar._hovered_actor == subject:
		fail("An offstage row must still hover without previewing its camera");return
	click(actor_point)
	if not main.social_ui.panel.visible or main.social_ui.profile_panel.selected_name != subject.npc_name or main.camera.target != saved_camera:
		fail("An offstage profile must open without moving the camera");return
	panel.open_board(board_id, true)
	panel.stage_button.pressed.emit()
	root.push_input(mouse, true)
	await process_frame
	if not actor_row.overlay.visible or actor_row.overlay_label.text != "加入":
		fail("The inactive role needs an Add overlay");return
	click(actor_point)
	stage_until = Time.get_ticks_msec() + 10000
	while (panel.stage_pending or actor_row.offstage) and Time.get_ticks_msec() < stage_until:
		await process_frame
	if panel.stage_pending or not subject.on_stage or not subject.visible or actor_row.offstage:
		fail("Selecting the inactive row must restore it to the world: " + panel.stage_status.text);return
	current_scene = main
	panel.reset_button.pressed.emit()
	await process_frame
	if not panel.reset_confirm.visible or (panel.pending and panel.operation == "reset") or api.world_id != old_world_id:
		fail("Reset must await user confirmation");return
	panel.reset_confirm.get_ok_button().pressed.emit()
	var reset_until := Time.get_ticks_msec() + 12000
	while (current_scene == main or current_scene == null) and Time.get_ticks_msec() < reset_until:
		await process_frame
	if current_scene == null or current_scene == main or api.world_id == old_world_id:
		fail("Confirmed reset must recreate the Godot scene for the new world: " + panel.status.text);return
	var restarted = current_scene
	for actor in restarted.npcs:
		var spawn: Dictionary = api.last_world.npcs[actor.npc_name]
		if actor.map_id != spawn.map_id or actor.global_position.distance_to(game.nav_world_of(spawn.room_id, spawn.map_id)) > 1.0:
			fail("Godot must place every NPC in the initial room");return
	if restarted.player_ui.active or restarted.notice_ui.visible or not api.backend_time.begins_with("第1天 08:"):
		fail("Reset must return to the initial clock and close player mode and god mode");return
	var restored := await fetch("/noticeboards/" + board_id.uri_encode() + "/notices")
	if not restored.get("notices", ["request failed"]).is_empty():
		fail("Old announcements survived a world reset");return
	print("PASS: distinct controls, notice publishing and reading, collapsible timeline, whisper, cast controls and confirmed world reset in Godot")
	quit(0)
