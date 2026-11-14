extends SceneTree
var http: HTTPRequest
var backend: String

func _initialize() -> void:
	call_deferred("verify")

func fail(message: String) -> void:
	push_error(message)
	quit(1)

func fetch(path: String, post := false) -> Dictionary:
	if http.request(backend + path, [], HTTPClient.METHOD_POST if post else HTTPClient.METHOD_GET) != OK:return {}
	var response: Array = await http.request_completed
	if response[0] != HTTPRequest.RESULT_SUCCESS or response[1] != 200:return {}
	var data = JSON.parse_string(response[3].get_string_from_utf8())
	return data if data is Dictionary else {}

func wait_ui(ui) -> bool:
	var until := Time.get_ticks_msec()+10000
	while ui.pending and Time.get_ticks_msec()<until:await process_frame
	return not ui.pending

func drive(ui) -> bool:
	ui._process(3.0)
	return await wait_ui(ui)

func type_text(control: Control, text: String) -> void:
	control.grab_focus()
	for character in text:
		var event := InputEventKey.new()
		event.pressed = true
		event.unicode = character.unicode_at(0)
		root.push_input(event, true)

func click(point: Vector2) -> void:
	for pressed in [true,false]:
		var event := InputEventMouseButton.new()
		event.position = point
		event.global_position = point
		event.button_index = MOUSE_BUTTON_LEFT
		event.pressed = pressed
		root.push_input(event,true)

func verify() -> void:
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	backend = OS.get_environment("ROAM_PLAYER_BACKEND")
	http = HTTPRequest.new()
	http.timeout = 0
	root.add_child(http)
	var api := root.get_node("ApiClient")
	api.set_process(false)
	api.backend_url = backend
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	for child in main.get_children():
		if child is CanvasLayer:child.set_process(false)
	for actor in main.npcs:actor.set_physics_process(false)
	main.social_ui.profile_panel.set_process(false)
	main.social_ui.panel.hide()
	var ui = main.player_ui
	ui.http.timeout = 0
	api.backend_online = true
	api.last_world = await fetch("/world")
	ui.mode_button.pressed.emit()
	if not ui.window.visible or not await drive(ui) or ui.active:
		fail("Player mode must open its panel but remain disabled by default");return
	ui.name_edit.clear()
	type_text(ui.name_edit,"测试访客")
	var caret: int = ui.name_edit.caret_column
	for cycle in range(3):ui._process(0)
	if ui.name_edit.caret_column != caret or not ui.name_edit.has_focus():
		fail("Player name input lost its caret while idle");return
	ui.join_button.pressed.emit()
	if not await wait_ui(ui) or not ui.active or ui.session_id.is_empty():
		fail("Joining through the actual player UI failed");return
	var session: String = ui.session_id
	if not await drive(ui):fail("Initial player history did not load");return
	# 已加入后，实际地图点击继续跟随/资料，并选择聊天对象。
	ui.window.hide()
	var subject = main.npcs[1]
	main.follow_npc(subject)
	await process_frame
	click(subject.sprite.get_global_transform_with_canvas() * Vector2(0,-15))
	if not ui.window.visible or ui.selected_npc != subject.npc_name or main.camera.target != subject:
		fail("Clicking an NPC in player mode must select its chat and keep follow/profile");return
	main.social_ui.panel.hide()
	if not await drive(ui):fail("NPC chat history did not load");return
	ui.message_edit.clear()
	type_text(ui.message_edit,"你好，能聊聊研究计划吗？")
	var first_draft: String = ui.message_edit.text
	# 刷新、角色切换和快捷键不能抢走草稿或移动镜头。
	var camera_before: Vector2 = main.camera.global_position
	main.camera.set_target(null)
	Input.action_press("ui_right")
	main.camera._process(1)
	Input.action_release("ui_right")
	if main.camera.global_position != camera_before:
		fail("Typing must not pan the camera");return
	var old_floor: String = main.current_map
	var old_debug: bool = main.get_node("DebugDraw").enabled
	for key in [KEY_1, KEY_V, KEY_SPACE]:
		var event := InputEventKey.new()
		event.pressed = true
		event.keycode = key
		event.unicode = key
		root.push_input(event,true)
		main._process(0)
	if main.current_map != old_floor or main.get_node("DebugDraw").enabled != old_debug or main.camera.target != null:
		fail("Chat input triggered floor/debug/follow shortcuts");return
	ui.message_edit.text = first_draft
	var original_index: int = ui.picker.selected
	ui._choose((original_index+1)%ui.picker.item_count)
	ui._choose(original_index)
	if ui.message_edit.text != first_draft:
		fail("Changing the chat recipient lost its draft");return
	var before: Dictionary = await fetch("/world")
	ui.send_button.pressed.emit()
	if not await wait_ui(ui) or ui.turn_id.is_empty():
		fail("Asynchronous send did not return an accepted turn");return
	var turn_id: String = ui.turn_id
	# 已接受后可继续输入下一句；轮询不能覆盖新草稿。
	ui.message_edit.text = "下一句草稿"
	var saw_processing := false
	var until := Time.get_ticks_msec()+12000
	while not ui.turn_id.is_empty() and Time.get_ticks_msec()<until:
		if not await drive(ui):break
		saw_processing = saw_processing or not ui.turn_id.is_empty()
		await process_frame
	if not ui.turn_id.is_empty() or not "欢迎来校园" in ui.transcript.text or not "研究计划" in ui.transcript.text:
		fail("NPC reply did not reach the actual chat transcript");return
	if ui.message_edit.text != "下一句草稿":
		fail("Polling overwrote the next input draft");return
	var after: Dictionary = await fetch("/world")
	if not saw_processing or after.minutes <= before.minutes or after.npcs[subject.npc_name].busy:
		fail("The world must keep running and release the NPC after replying");return
	var memory := await fetch("/player/memory?session_id="+session)
	if memory.get("active",[]).size() != 1 or not "欢迎来校园" in str(memory.active[0].content):
		fail("Player personal memory did not contain both sides of the exchange");return
	var saved := await fetch("/player/turns/"+turn_id+"?session_id="+session)
	if saved.status != "completed" or saved.memories.size() != 2:
		fail("Both participants must receive exactly one completed memory");return
	# 忙碌返回清楚提示，原稿仍在；不抢占已经运行的NPC任务。
	await fetch("/test/player/busy/"+subject.npc_name.uri_encode()+"?value=true",true)
	ui.send()
	if not await wait_ui(ui) or not "对方正在" in ui.status.text or ui.message_edit.text != "下一句草稿":
		fail("A busy NPC must refuse clearly and preserve the draft");return
	await fetch("/test/player/busy/"+subject.npc_name.uri_encode()+"?value=false",true)
	# 已成功轮次的旧回包不得清空后来输入的文本。
	ui.submitted_text = "旧稿"
	ui.request_kind = "send"
	ui._done(HTTPRequest.RESULT_SUCCESS,202,PackedStringArray(),JSON.stringify(saved).to_utf8_buffer())
	if ui.message_edit.text != "下一句草稿":
		fail("A late response cleared newer input");return
	# 通过地图切换聊天对象时，旧发送回包也不能清空新对象的同文草稿。
	ui._choose((original_index+1)%ui.picker.item_count)
	ui.message_edit.text = "另一位的同文草稿"
	ui.submitted_text = ui.message_edit.text
	ui.request_kind = "send"
	ui._done(HTTPRequest.RESULT_SUCCESS,202,PackedStringArray(),JSON.stringify(saved).to_utf8_buffer())
	if ui.message_edit.text != "另一位的同文草稿":
		fail("An old recipient response cleared the new recipient's draft");return
	ui._choose(original_index)
	await process_frame
	await process_frame
	if not Rect2(Vector2.ZERO,root.get_visible_rect().size).encloses(ui.window.get_global_rect()):
		fail("The player chat panel must fit inside the viewport");return
	var preview := OS.get_environment("ROAM_PLAYER_PREVIEW")
	if not preview.is_empty():
		ui.status.text = "本轮交谈已保存；NPC已继续原行程。"
		await process_frame
		await RenderingServer.frame_post_draw
		if root.get_texture().get_image().save_png(preview) != OK:
			fail("Could not save the actual player chat preview");return
	ui.leave_button.pressed.emit()
	if not await wait_ui(ui) or ui.active:
		fail("Leaving player mode failed");return
	ui.join_button.pressed.emit()
	if not await wait_ui(ui) or not ui.active or ui.session_id == session:
		fail("Rejoining should restore identity with a new session");return
	if not await drive(ui) or not "欢迎来校园" in ui.transcript.text:
		fail("Rejoining lost the previous chat history");return
	api.backend_online = false
	ui._process(0)
	if not ui.send_button.disabled or not "后端未连接" in ui.status.text or ui.message_edit.text != "下一句草稿":
		fail("Offline state must disable sending and preserve drafts");return
	print("PASS: mode off by default, join, actual NPC click -> chat, input and per-NPC drafts, world continues during reply, both memories, busy refusal, relation display, leave/rejoin history, offline fallback")
	quit(0)
