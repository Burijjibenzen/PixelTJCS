extends SceneTree
## 真实HTTP查询公开资料；可选输出实际渲染截图供布局验收。
func _initialize() -> void:
	call_deferred("verify")

func wait_profile(panel, npc_name: String) -> bool:
	var deadline := Time.get_ticks_msec() + 15000
	while Time.get_ticks_msec() < deadline:
		if panel.cache.has(npc_name) and not panel.pending:
			return true
		await create_timer(0.03).timeout
	return false

func verify() -> void:
	var api := root.get_node("ApiClient")
	api.set_process(false)
	api.backend_online = false
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	main.set_physics_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
	var ui
	for child in main.get_children():
		if child.get_script() and child.get_script().resource_path.ends_with("social_ui.gd"):
			ui = child
	ui.set_process(false)
	ui.history_pending = true
	var panel = ui.profile_panel
	panel.backend_url = OS.get_environment("ROAM_PROFILE_BACKEND")
	api.last_world = JSON.parse_string(OS.get_environment("ROAM_PROFILE_WORLD"))
	api.backend_online = true
	var names: Array = JSON.parse_string(OS.get_environment("ROAM_PROFILE_NAMES"))
	for npc_name in names:
		for index in ui.picker.item_count:
			if ui.picker.get_item_text(index) == npc_name:
				ui.picker.select(index)
				ui.picker.item_selected.emit(index)
		if not await wait_profile(panel, npc_name):
			push_error("Profile HTTP timeout: " + npc_name)
			quit(1)
			return
		if panel.name_label.text != npc_name or panel.title_label.text.is_empty() or panel.schedule_box.get_child_count() == 0:
			push_error("Profile HTTP response did not reach visual panel")
			quit(2)
			return
	var preview := OS.get_environment("ROAM_PROFILE_PREVIEW")
	if not preview.is_empty():
		await process_frame
		await process_frame
		await RenderingServer.frame_post_draw
		var screenshot := root.get_texture().get_image()
		if screenshot.is_empty() or screenshot.save_png(preview) != OK:
			push_error("Profile screenshot could not be saved")
			quit(3)
			return
	print("PASS: real public profile HTTP, actor selection, live location and rendered profile panel")
	quit(0)
