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

func center(actor) -> Vector2:
	return actor.sprite.get_global_transform_with_canvas() * Vector2.ZERO

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
	ui.history_pending = true
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
	click(center(first))
	if not check(main.camera.target == first and ui.panel.visible, "clicking a character must follow it and open the observer"):return
	if not check(ui.tabs.current_tab == 5 and ui.profile_panel.selected_name == first.npc_name and ui.picker.get_item_text(ui.picker.selected) == first.npc_name, "click selects the matching character profile"):return
	ui.tabs.current_tab = 0
	await process_frame
	click(center(second))
	if not check(main.camera.target == second and ui.tabs.current_tab == 5 and ui.profile_panel.selected_name == second.npc_name, "clicking another NPC switches follow and returns to profile"):return
	await process_frame
	await process_frame
	# 在标题栏背后放一名NPC，GUI点击和拖动不能误选它。
	var header := ui.drag_handle.get_global_rect().get_center() as Vector2
	first.global_position = main.get_global_transform_with_canvas().affine_inverse() * header - first.sprite.position
	click(header)
	if not check(main.camera.target == second, "observer controls must block clicks to characters behind them"):return
	var before: Vector2 = ui.panel.position
	button(header, true)
	motion(header + Vector2(-500, 20))
	button(header + Vector2(-500, 20), false)
	if not check(ui.panel.position.x < before.x - 100 and not ui._dragging and main.camera.target == second, "header dragging moves the panel without changing the followed NPC"):return
	var moved: Vector2 = ui.panel.position
	ui.panel.hide()
	ui.show_profile(second.npc_name)
	if not check(ui.panel.position.is_equal_approx(moved), "reopening observer preserves its dragged position"):return
	header = ui.drag_handle.get_global_rect().get_center()
	button(header, true)
	motion(Vector2(-2000, -2000))
	button(Vector2(-2000, -2000), false)
	if not check(ui.panel.position == Vector2.ZERO, "dragging beyond top/left edges keeps the panel reachable"):return
	header = ui.drag_handle.get_global_rect().get_center()
	button(header, true)
	motion(Vector2(5000, 5000))
	button(Vector2(5000, 5000), false)
	if not check(Rect2(Vector2.ZERO, root.get_visible_rect().size).encloses(ui.panel.get_global_rect()), "dragging beyond bottom/right edges clamps the full panel to the viewport"):return
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
	var other_map := "jishilou_floor4"
	second.map_id = other_map
	second.floor_changed.emit(second)
	if not check(main.current_map == other_map and main.camera.target == second, "mouse-selected follow continues across floors"):return
	main.cancel_follow()
	if not check(main.camera.target == null, "existing cancel-follow remains usable"):return
	main.cycle_follow()
	if not check(main.camera.target == main.npcs[(main.npcs.find(second) + 1) % main.npcs.size()], "keyboard follow cycle continues after the clicked NPC"):return
	print("PASS: draggable observer, viewport bounds, preserved position, UI click blocking, NPC click -> follow/profile, zoom, cross-floor follow and existing follow controls")
	quit(0)
