extends SceneTree
## 公开资料页的状态、快速切换、缺字段和长文本布局回归。
class Actor extends Node2D:
	var npc_name: String

func _initialize() -> void:
	call_deferred("verify")

func check(ok: bool, label: String) -> bool:
	if not ok:
		push_error("FAIL: " + label)
		quit(1)
	return ok

func payload(npc_name: String) -> Dictionary:
	return {"name": npc_name, "role": "student", "title": npc_name + "的公开身份", "expertise": "公开专长",
		"traits": ["认真", "乐于讨论"], "mbti": "ENTJ", "tone": "简洁", "catchphrase": "再核对一下",
		"goals": {"short_term": "近期公开目标", "long_term": "长期公开目标"},
		"schedule": [{"time": "08:00", "location": {"name": "公开地点"}}],
		"location": {"name": "当前位置"}, "destination": {"name": "目的地"}, "secrets": ["PRIVATE_UI_SENTINEL"]}

func reply(panel, npc_name: String, data: Dictionary) -> void:
	panel.request_name = npc_name
	panel.pending = true
	panel._profile_done(HTTPRequest.RESULT_SUCCESS, 200, PackedStringArray(), JSON.stringify(data).to_utf8_buffer())

func all_text(node: Node) -> String:
	var result := str(node.text) if node is Label else ""
	for child in node.get_children():
		result += all_text(child)
	return result

func verify() -> void:
	var api := root.get_node("ApiClient")
	api.set_process(false)
	api.backend_online = false
	var actors: Array = []
	for npc_name in ["A", "B", "C"]:
		var actor := Actor.new()
		actor.npc_name = npc_name
		root.add_child(actor)
		actors.append(actor)
	var panel = load("res://scripts/persona_panel.gd").new()
	panel.setup(actors)
	root.add_child(panel)
	panel.set_process(false)
	panel.position = Vector2(12, 12)
	panel.size = Vector2(480, 530)
	panel.select_npc("A")
	if not check("后端未连接" in panel.state_label.text and panel.refresh_button.disabled, "offline state is usable without backend or secret data"): return
	panel.request_name = "A"
	panel.pending = true
	panel.select_npc("B")
	panel.hide()
	reply(panel, "A", payload("A"))
	if not check(panel.name_label.text == "B" and not "A的公开身份" in panel.title_label.text, "late response cannot overwrite newly selected actor"): return
	panel.show()
	api.backend_online = true
	reply(panel, "B", payload("B"))
	if not check(panel.title_label.text == "B的公开身份" and panel.schedule_box.get_child_count() == 1, "public fields and schedule render"): return
	if not check(not "PRIVATE_UI_SENTINEL" in all_text(panel), "unknown private fields never render"): return
	api.last_world = {"npcs": {"B": {"map_id": "zhixinguan_floor2", "room_id": "zx2_lib", "target_map_id": "jishilou_floor4", "target_room_id": "R05"}}}
	panel._render_location()
	if not check("智信馆" in panel.location_label.text and "济事楼" in panel.destination_label.text, "live world updates location and destination independently"): return
	api.last_world.npcs.B.activity = {"title": "基础作息", "state_label": "正在前往", "reason": "基础作息", "detail": "正在前往目的地"}
	panel._render_location()
	for field in ["当前活动：基础作息", "活动状态：正在前往", "安排依据：基础作息", "行程说明：正在前往目的地"]:
		if not check(field in panel.activity_label.text, "activity properties have explicit readable labels"): return
	api.last_world.npcs.B.target_map_id = "zhixinguan_floor2"
	api.last_world.npcs.B.target_room_id = "zx2_lib"
	panel._render_location()
	if not check(panel.destination_label.text.begins_with("目的地：") and "已在目的地" in panel.destination_label.text, "destination stays labelled even after arrival"): return
	api.backend_online = false
	panel._update_state()
	if not check("上次同步" in panel.state_label.text and panel.title_label.text == "B的公开身份", "cached public profile remains available offline"): return
	panel.select_npc("C")
	api.backend_online = true
	reply(panel, "C", {"name": "C", "role": "student", "title": "最小资料", "traits": null, "goals": null, "schedule": null})
	if not check(panel.short_goal.text == "未填写" and panel.schedule_box.get_child_count() == 1, "missing optional fields render safely"): return
	var long_data := payload("C")
	long_data.goals.short_term = "[b]长文本原样显示，不作为富文本执行。".repeat(80)
	long_data.traits = ["很长的性格标签也应正常换行".repeat(5)]
	long_data.schedule = []
	for i in range(20):
		long_data.schedule.append({"time": "%02d:00" % i, "location": {"name": "长日程地点".repeat(12)}})
	reply(panel, "C", long_data)
	await process_frame
	await process_frame
	var scroll: ScrollContainer = panel.get_child(2)
	if not check(scroll.get_v_scroll_bar().max_value > scroll.size.y, "long profile content is scrollable"): return
	if not check(scroll.size.x <= panel.size.x and scroll.get_child(0).size.x <= scroll.size.x + 1, "long text and chips fit panel width"): return
	if not check("[b]" in panel.short_goal.text, "profile text is literal rather than markup"): return
	panel.request_name = "C"
	panel._profile_done(HTTPRequest.RESULT_SUCCESS, 404, PackedStringArray(), "{}".to_utf8_buffer())
	if not check("未找到" in panel.state_label.text, "unknown actor has a clear error state"): return
	api.backend_online = false
	var ui = load("res://scripts/social_ui.gd").new()
	ui.setup(actors)
	root.add_child(ui)
	ui.set_process(false)
	ui.profile_panel.set_process(false)
	ui.picker.select(1)
	ui.picker.item_selected.emit(1)
	if not check(ui.tabs.current_tab == 5 and ui.profile_panel.selected_name == "B", "shared actor picker drives the visual profile tab"): return
	if not check(not ui.history_slider.visible and not ui.meeting_status.visible, "profile view gives details room without unrelated meeting controls"): return
	ui.tabs.current_tab = 0
	if not check(ui.history_slider.visible and ui.meeting_status.visible, "existing memory and meeting controls return on other tabs"): return
	print("PASS: public profile UI, offline/cache/error states, rapid selection, optional fields, live location, safe text, scrolling")
	quit(0)
