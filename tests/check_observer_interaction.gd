extends SceneTree
## 使用Viewport真实输入分发，验证拖动、UI遮挡、点击人物与资料联动。

func _initialize() -> void:
	call_deferred("verify")

func check(ok: bool, message: String) -> bool:
	if not ok:
		push_error(message)
		quit(1)
	return ok

func button(point: Vector2, pressed: bool) -> void:
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.button_mask = MOUSE_BUTTON_MASK_LEFT if pressed else 0
	event.pressed = pressed
	event.position = point
	event.global_position = point
	root.push_input(event, true)

func click(point: Vector2) -> void:
	button(point, true)
	button(point, false)

func motion(point: Vector2) -> void:
	var event := InputEventMouseMotion.new()
	event.position = point
	event.global_position = point
	event.button_mask = MOUSE_BUTTON_MASK_LEFT
	root.push_input(event, true)

func wheel(point: Vector2, direction: MouseButton) -> void:
	var event := InputEventMouseButton.new()
	event.button_index = direction
	event.pressed = true
	event.position = point
	event.global_position = point
	root.push_input(event, true)

func center(actor) -> Vector2:
	return actor.sprite.get_global_transform_with_canvas() * Vector2.ZERO

func check_window(entry: Button, surface: Control, header: Control, window) -> bool:
	click(entry.get_global_rect().get_center())
	await process_frame
	if not check(window.visible, entry.text + " opens on the first click"):return false
	click(entry.get_global_rect().get_center())
	if not check(not window.visible, "%s closes on the second click with %s and panel %s" % [entry.text, entry.get_global_rect(), surface.get_global_rect()]):return false
	click(entry.get_global_rect().get_center())
	await process_frame
	await process_frame
	var title: Control = header.get_child(0)
	var close: Button = header.get_child(-1)
	var start := surface.position
	var point := title.get_global_rect().get_center()
	button(point, true)
	motion(point + Vector2(-72, 18))
	button(point + Vector2(-72, 18), false)
	if not check(surface.position.x < start.x - 50, entry.text + " moves by its title"):return false
	await process_frame
	click(close.get_global_rect().get_center())
	return check(close.text == "X" and not window.visible, entry.text + " closes with X")

func verify() -> void:
	var api := root.get_node("ApiClient")
	api.set_process(false)
	api.backend_online = false
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	main.set_physics_process(false)
	main.camera.set_process(false)
	var ui = main.social_ui
	ui.set_process(false)
	ui.profile_panel.set_process(false)
	var style: StyleBoxFlat = ui.panel.get_theme_stylebox("panel")
	if not check(style.corner_radius_top_left == 8 and style.corner_radius_top_right == 8 and style.corner_radius_bottom_left == 8 and style.corner_radius_bottom_right == 8, "observer panel has four rounded corners"):return
	for actor in main.npcs:
		actor.set_physics_process(false)
		actor.global_position += Vector2(600, 600)
	var first = main.npcs[0]
	var second = main.npcs[1]
	var game := root.get_node("Game")
	first.global_position = game.nav_world_of(first.home_room, first.map_id)
	second.map_id = first.map_id
	second.global_position = first.global_position + Vector2(-140, 0)
	main._show_floor(first.map_id, first.global_position)
	await process_frame
	await process_frame
	ui.panel.hide()
	motion(center(first))
	await process_frame
	await process_frame
	if not check(main.interaction_marks.sprites == [first.sprite], "hovering the clickable NPC outlines it"):return
	click(center(first))
	if not check(main.camera.target == first and ui.panel.visible, "clicking a character must follow it and open the observer"):return
	if not check(ui.tabs.current_tab == 5 and ui.profile_panel.selected_name == first.npc_name and ui.picker.get_item_text(ui.picker.selected) == first.npc_name, "click selects the matching character profile"):return
	ui.tabs.current_tab = 0
	await process_frame
	click(center(second))
	if not check(main.camera.target == second and ui.tabs.current_tab == 5 and ui.profile_panel.selected_name == second.npc_name, "clicking another NPC switches follow and returns to profile"):return
	await process_frame
	await process_frame
	var profile_size: Vector2 = ui.panel.size
	var tab_bar: TabBar = ui.tabs.get_tab_bar()
	var last_tab := tab_bar.get_tab_rect(ui.tabs.get_tab_count() - 1)
	if not check(last_tab.size.x > 0 and last_tab.end.x <= tab_bar.size.x, "all tabs fit on the same row"):return
	var zoom_before: Vector2 = main.camera.zoom
	for index in ui.tabs.get_tab_count():
		ui.tabs.current_tab = index
		await process_frame
		await process_frame
		if not check(ui.panel.size == profile_size, "every observer page keeps the profile window size"):return
		var panel_point: Vector2 = ui.tabs.get_global_rect().get_center()
		wheel(panel_point, MOUSE_BUTTON_WHEEL_UP)
		wheel(panel_point, MOUSE_BUTTON_WHEEL_DOWN)
		if not check(main.camera.zoom == zoom_before, "scrolling a panel edge does not zoom the map"):return
	ui.tabs.current_tab = 5
	await process_frame
	# 在标题栏背后放一名NPC，GUI点击和拖动不能误选它。
	var header := ui.drag_handle.get_global_rect().get_center() as Vector2
	first.global_position = main.get_global_transform_with_canvas().affine_inverse() * header - first.sprite.position
	motion(header)
	await process_frame
	await process_frame
	if not check(main.interaction_marks.sprites.is_empty(), "observer controls block hover outlines behind them"):return
	click(header)
	if not check(main.camera.target == second, "observer controls must block clicks to characters behind them"):return
	var before: Vector2 = ui.panel.position
	button(header, true)
	motion(header + Vector2(-500, 20))
	button(header + Vector2(-500, 20), false)
	if not check(ui.panel.position.x < before.x - 100 and not ui.panel_drag.dragging and main.camera.target == second, "header dragging moves the panel without changing the followed NPC"):return
	var moved: Vector2 = ui.panel.position
	ui.panel.hide()
	ui.show_profile(second.npc_name)
	if not check(ui.panel.position.is_equal_approx(moved), "reopening observer preserves its dragged position"):return
	header = ui.drag_handle.get_global_rect().get_center()
	button(header, true)
	motion(Vector2(-2000, -2000))
	button(Vector2(-2000, -2000), false)
	var viewport := root.get_visible_rect()
	if not check(ui.panel.position.x < 0 and ui.panel.position.y < 0, "the panel can move off the top/left edges"):return
	header = ui.drag_handle.get_global_rect().intersection(viewport).get_center()
	button(header, true)
	motion(Vector2(5000, 5000))
	button(Vector2(5000, 5000), false)
	if not check(ui.panel.get_global_rect().end.x > viewport.end.x and ui.panel.get_global_rect().end.y > viewport.end.y
			and ui.drag_handle.get_global_rect().intersects(viewport), "offscreen panels keep part of the draggable title visible"):return
	# 缩放后的点击仍以实际人物画面为准。
	ui.panel.hide()
	main.camera.zoom = Vector2(2, 2)
	main._show_floor(second.map_id, second.global_position)
	await process_frame
	click(center(second))
	if not check(main.camera.target == second and ui.profile_panel.selected_name == second.npc_name, "character picking works with camera zoom"):return
	click(second.label.get_global_transform_with_canvas() * (second.label.size * 0.5))
	if not check(main.camera.target == second, "clicking the name label selects its character too"):return
	var hidden_actor = main.npcs.back()
	hidden_actor.map_id = "jishilou_floor4"
	hidden_actor.global_position = second.global_position + Vector2(0, 2)
	click(center(second))
	if not check(main.camera.target == second, "other floors cannot intercept a visible character click"):return
	ui.panel.hide()
	var start := center(second)
	var camera_before: Vector2 = main.camera.global_position
	button(start, true)
	motion(start + Vector2(-80, -28))
	button(start + Vector2(-80, -28), false)
	if not check(main.camera.global_position != camera_before and main.camera.target == null and not ui.panel.visible,
			"dragging from an NPC moves the camera without opening its profile"):return
	var open_space := Vector2(570, 730)
	zoom_before = main.camera.zoom
	wheel(open_space, MOUSE_BUTTON_WHEEL_UP)
	if not check(main.camera.zoom > zoom_before, "wheel zoom still works on the map"):return
	main.follow_npc(second)
	var other_map := "jishilou_floor4"
	second.map_id = other_map
	second.floor_changed.emit(second)
	if not check(main.current_map == other_map and main.camera.target == second, "mouse-selected follow continues across floors"):return
	main.cancel_follow()
	if not check(main.camera.target == null, "existing cancel-follow remains usable"):return
	main.cycle_follow()
	if not check(main.camera.target == main.npcs[(main.npcs.find(second) + 1) % main.npcs.size()], "keyboard follow cycle continues after the clicked NPC"):return
	ui.panel.hide()
	var god = main.notice_ui
	var god_surface: Control = god.get_node("Window")
	var mechanisms = main.get_node("MechanicsPanel")
	var map = main.get_node("MapBrowser")
	var map_surface: Control = map.get_node("Background")
	var mode_bar: Control = main.get_node("UI/ModeBar")
	for item in [
		[main.player_ui.mode_button, main.player_ui.window, main.player_ui.window.get_child(0).get_child(0), main.player_ui.window],
		[mode_bar.get_node("GodMode"), god_surface, god.heading_title.get_parent(), god],
		[mechanisms.get_node("Open"), mechanisms.window, mechanisms.window.get_child(0).get_child(0), mechanisms.window],
		[mode_bar.get_node("MapBrowserButton"), map_surface, map_surface.get_node("Toolbar"), map],
	]:
		if not await check_window(item[0], item[1], item[2], item[3]):return
	if not check(not paused, "closing the embedded map restores the main scene"):return
	var sidebar = main.character_status
	var side_heading: Control = sidebar._panel.get_child(0).get_child(0)
	var side_title: Vector2 = side_heading.get_child(0).get_global_rect().get_center()
	button(side_title, true)
	motion(side_title + Vector2(65, 15))
	button(side_title + Vector2(65, 15), false)
	if not check(sidebar.position.x > 50, "character sidebar moves by its title"):return
	await process_frame
	var side_close: Button = side_heading.get_child(-1)
	click(side_close.get_global_rect().get_center())
	if not check(side_close.text == "X" and sidebar._handle.visible, "sidebar collapses with X"):return
	click(sidebar._handle.get_global_rect().get_center())
	if not check(sidebar._panel.visible and sidebar.position.x > 50, "sidebar reopens at the dragged position"):return
	print("PASS: observer and character controls, click-to-toggle panels, X, title dragging and viewport bounds")
	quit(0)
