extends SceneTree

func _initialize() -> void:
	call_deferred("verify")

func check(ok: bool, detail: String) -> bool:
	if not ok:
		push_error(detail)
		quit(1)
	return ok

func move_mouse(point: Vector2) -> void:
	var event := InputEventMouseMotion.new()
	event.position = point
	root.push_input(event, true)

func click(point: Vector2) -> void:
	for pressed in [true, false]:
		var event := InputEventMouseButton.new()
		event.button_index = MOUSE_BUTTON_LEFT
		event.position = point
		event.pressed = pressed
		root.push_input(event, true)

func settle() -> void:
	await process_frame
	await process_frame

func verify() -> void:
	var api := root.get_node("ApiClient")
	var game := root.get_node("Game")
	api.set_process(false)
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	main.set_physics_process(false)
	main.camera.set_process(false)
	main.social_ui.set_process(false)
	main.social_ui.panel.hide()
	main.player_ui.set_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
	var sidebar = main.character_status
	if not check(sidebar._rows.size() == 11 and not sidebar._player_row.visible, "Ten NPC rows; player row appears only after joining"): return
	main.player_ui.profile_name = "测试访客"
	main.player_ui.active = true
	main._process(0.0)
	sidebar._refresh_rows()
	if not check(sidebar._player_row.visible and sidebar._player_row.name_label.text == "测试访客", "Joined player's name appears in the top row"): return
	main.notice_ui.show()
	sidebar._refresh_rows()
	if not check(sidebar._player_row.status_label.text == "阅读公告中", "The player row reports reading the open notice board"): return
	main.notice_ui.hide()
	var stairwell: Vector2 = game.nav_world_of("zx2_st_w", game.ZHIXING)
	if not check(game.status_location_at(stairwell, game.ZHIXING) == "智信馆·走廊", "Zhixing stairwell is a corridor in the status label"): return
	var door: Vector2i = game.floor_data[game.FLOOR3].door_cells[0]
	if not check(game.status_location_at(game.world_of(door), game.FLOOR3).ends_with("·走廊"), "Doorways use the corridor label"): return
	var reader = main.npcs[0]
	reader.map_id = game.FLOOR3
	reader.global_position = game.world_of(Vector2i(60, 33))
	reader.go_to_location(game.FLOOR4, "R05")
	reader.social_busy = true
	var traveller_row = sidebar._rows[1]
	api.backend_online = true
	api.last_world = {"npcs": {reader.npc_name: {"activity": {"source": "notice", "state": "active"}}}}
	sidebar._refresh_rows()
	if not check(traveller_row.status_label.text == "阅读公告中", "Reading takes precedence over an existing route"): return
	api.last_world.npcs[reader.npc_name] = {"conversation_partner": main.npcs[1].npc_name}
	sidebar._refresh_rows()
	if not check(traveller_row.status_label.text == "与%s交谈" % main.npcs[1].npc_name, "Chat names the partner before the route"): return
	api.backend_online = false
	reader.social_busy = false
	sidebar._refresh_rows()
	if not check(traveller_row.status_label.text == "前往学术讲座教室", "Offline route remains visible"): return

	var other = main.npcs[1]
	other.map_id = game.ZHIXING
	other.global_position = stairwell
	var other_row = sidebar._rows[2]
	main.player_avatar.global_position = game.world_of(Vector2i(66, 33))
	main._focus_player()
	main.camera.zoom = Vector2(1.5, 1.5)
	var original_position: Vector2 = main.camera.global_position
	await settle()
	var other_point: Vector2 = other_row.get_global_rect().get_center()
	move_mouse(other_point)
	await settle()
	if not check(main.camera.target == other and main.current_map == game.ZHIXING and sidebar._panel_hovered, "Row hover previews another floor and highlights the sidebar"): return
	var event := InputEventKey.new()
	event.keycode = KEY_LEFT
	event.pressed = true
	root.push_input(event)
	main._physics_process(0.2)
	event.pressed = false
	root.push_input(event)
	if not check(game.cell_of(main.player_avatar.global_position, game.FLOOR3) == Vector2i(65, 33) and main.camera.target == other, "Preview preserves actual player walking and camera lock"): return
	other.map_id = game.FLOOR4
	other.global_position = game.nav_world_of("R01", game.FLOOR4)
	other.floor_changed.emit(other)
	if not check(main.current_map == game.FLOOR4 and main.camera.target == other, "Hover follows a character changing floors"): return
	move_mouse(traveller_row.get_global_rect().get_center())
	await settle()
	move_mouse(Vector2(640, 560))
	await settle()
	if not check(main.current_map == game.FLOOR3 and main.camera.target == main.player_avatar
		and main.camera.global_position == original_position and main.camera.zoom == Vector2(1.5, 1.5), "Leaving consecutive row previews restores the original view"): return

	move_mouse(other_point)
	await settle()
	click(other_point)
	move_mouse(Vector2(640, 560))
	await settle()
	if not check(main.current_map == game.FLOOR4 and main.camera.target == other
		and main.social_ui.panel.visible and main.social_ui.profile_panel.selected_name == other.npc_name,
		"Row click keeps the selected view and opens the existing profile"): return
	click(sidebar._player_row.get_global_rect().get_center())
	if not check(main.current_map == game.FLOOR3 and main.camera.target == main.player_avatar and main.player_ui.window.visible,
		"Player row returns to the player and opens the player panel"): return
	main.player_ui._close_panel()
	main.social_ui.panel.hide()
	move_mouse(other_point)
	await settle()
	sidebar._set_expanded(false)
	await settle()
	if not check(not sidebar._panel.visible and sidebar._handle.visible and main.camera.target == main.player_avatar,
		"Collapsing restores a preview and shows the left handle"): return
	var handle_point: Vector2 = sidebar._handle.get_global_rect().get_center()
	move_mouse(handle_point)
	await settle()
	if not check(sidebar._handle.is_hovered() and sidebar._handle.stroke_width == 2.0, "Trapezoid handle has the shared two-pixel hover stroke"): return
	click(handle_point)
	if not check(sidebar._panel.visible and not sidebar._handle.visible, "Clicking the trapezoid opens the sidebar"): return
	print("PASS: status rows, reading and chat priority, cross-floor preview and restore, profile clicks, player control, collapse and expand")
	quit(0)
