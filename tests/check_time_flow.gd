extends SceneTree
## 使用真实 NPC 物理移动验证倍速，显示时钟则使用确定的现实秒数验证。

func _initialize() -> void:
	call_deferred("verify")

func check(ok: bool, label: String) -> bool:
	if not ok:
		push_error("FAIL: " + label)
		quit(1)
	return ok

func verify() -> void:
	var api := root.get_node("ApiClient")
	api.set_process(false)
	api.backend_online = false
	api.simulation_paused = false
	api.simulation_speed = 10.0
	api.display_minutes = 480.0
	api._advance_display_clock(2.0)
	if not check(is_equal_approx(api.display_minutes, 500.0), "original backend clock: 1 real second = 10 game minutes"): return
	api.set_speed(40.0)
	if not check(is_equal_approx(api.display_minutes, 500.0), "changing speed cannot jump time"): return
	api._advance_display_clock(2.0)
	if not check(is_equal_approx(api.display_minutes, 580.0), "fourfold continuous clock"): return
	api.simulation_paused = true
	api._advance_display_clock(20.0)
	if not check(is_equal_approx(api.display_minutes, 580.0), "paused clock remains fixed"): return
	api.simulation_paused = false
	api._clock_initialized = true
	api._apply_world({"minutes": 578.0, "time": "09:38", "npcs": {}, "social": {"speed": 10.0}})
	if not check(is_equal_approx(api.display_minutes, 580.0), "late server sample cannot rewind displayed clock"): return
	api._advance_display_clock(0.1)
	if not check(api.display_minutes >= 580.0 and api.display_minutes < 582.0, "clock correction stays continuous"): return
	api.backend_online = false
	api._clock_correction = 0.0
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	main.set_physics_process(false)
	for child in main.get_children():
		if child is CanvasLayer:
			child.set_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
	var subject = main.npcs[0]
	# 在地图外空场地测物理距离，不让墙体和随机路线干扰倍率测量。
	var start := Vector2(-10000, -10000)
	var distances: Array[float] = []
	for rate in [10.0, 40.0, 60.0]:
		api.simulation_speed = rate
		api.simulation_paused = false
		subject.global_position = start
		subject.path.clear()
		for i in range(100):
			subject.path.append(start + Vector2(i * 16, 0))
		subject.path_i = 0
		subject.state = subject.S.WALK
		subject.social_busy = false
		for i in range(30):
			await physics_frame
			subject._physics_process(1.0 / 60.0)
		distances.append(subject.global_position.distance_to(start))
	if not check(absf(distances[1] / distances[0] - 4.0) < 0.05, "fourfold speed moves fourfold distance across grid centers"): return
	if not check(absf(distances[2] / distances[0] - 6.0) < 0.05, "three-day fast forward moves sixfold distance across grid centers"): return
	var paused_at: Vector2 = subject.global_position
	api.simulation_paused = true
	await physics_frame
	subject._physics_process(1.0 / 60.0)
	if not check(subject.global_position == paused_at, "world pause also pauses character movement"): return
	api.simulation_paused = false
	var observer
	for child in main.get_children():
		if child.get_script() and child.get_script().resource_path.ends_with("social_ui.gd"):
			observer = child
	observer._show_line({"conversation_id": "moving", "game_event_id": "test-event", "speech": "event_self_talk",
		"sequence": 1, "participants": [subject.npc_name], "speaker": subject.npc_name, "text": "（自言自语）路上想个问题。"})
	await physics_frame
	subject._physics_process(1.0 / 60.0)
	if not check(subject.global_position != paused_at and not observer.playbacks.is_empty(), "self-talk does not block movement"): return
	var line := {"conversation_id": "talking", "game_event_id": "test-chat", "speech": "npc_dialogue",
		"sequence": 1, "participants": [subject.npc_name, main.npcs[1].npc_name],
		"speaker": subject.npc_name, "text": "说完后还可以看气泡。", "ordered": true}
	subject.social_busy = true
	observer._show_line(line)
	observer._tick_playbacks(7.0)
	if not check(subject.movement_locked(), "conversation remains locked by backend after a bubble expires"): return
	subject.social_busy = false
	if not check(not subject.movement_locked() and not observer.bubble_labels[subject.npc_name].visible,
			"actor can move after the last bubble and backend release"): return
	print("PASS: original 10 game minutes/real second, continuous rates, movement x4/x6, pause, nonblocking bubbles")
	quit(0)
