extends SceneTree

func _initialize() -> void:
	call_deferred("verify")

func check(ok: bool, detail: String) -> bool:
	if not ok:
		push_error(detail)
		quit(1)
	return ok

func target(floor_id: String, room: String, point: Vector2) -> Dictionary:
	return {"map_id": floor_id, "room_id": room, "target_map_id": floor_id,
		"target_room_id": room, "target_position": {"x": point.x, "y": point.y}}

func verify() -> void:
	var api := root.get_node("ApiClient")
	var game := root.get_node("Game")
	api.set_process(false)
	var main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	main.set_process(false)
	main.set_physics_process(false)
	main.social_ui.set_process(false)
	main.social_ui.profile_panel.set_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
	var a = main.npcs[0]
	var b = main.npcs[1]
	var floor_id: String = game.FLOOR3
	var room := "R01"
	var start := Vector2(296, 488)
	var original_goal := start + Vector2(64, 0)
	var new_goal := start + Vector2(80, 0)
	for npc in [a, b]:
		npc.map_id = floor_id
		npc.goal_map_id = floor_id
		npc.goal_room = room
		npc._sections.clear()
		npc._idle()
	a.global_position = start
	b.global_position = start + Vector2(0, 16)
	api._npcs = [a, b]
	api.backend_online = true
	api._sending_position = {"npc": "fixture"}
	api._apply_world({"npcs": {a.npc_name: target(floor_id, room, original_goal)}})
	for i in 6:
		await physics_frame
		a._physics_process(1.0 / 60.0)
	var before_retarget: Vector2 = a.global_position
	api._apply_world({"npcs": {a.npc_name: target(floor_id, room, new_goal)}})
	await physics_frame
	a._physics_process(1.0 / 60.0)
	if not check(a.global_position.x > before_retarget.x and before_retarget.x > start.x,
			"Retargeting ahead from between tile centers must continue forward"): return

	# 已经走到约见点，等待答复的快照没有精确落点，缓存仍须保留。
	a.global_position = new_goal
	b.global_position = new_goal - Vector2(16, 0)
	a._sections.clear()
	a._idle()
	var a_position: Vector2 = a.global_position
	var b_position: Vector2 = b.global_position
	var waiting := {"map_id": floor_id, "room_id": room, "target_map_id": floor_id,
		"target_room_id": room, "busy": true}
	api._apply_world({"npcs": {a.npc_name: waiting, b.npc_name: waiting}})
	if not check(api._point_targets[a.npc_name] == new_goal and api._pending_positions[a.npc_name].arrived,
			"An invitation snapshot cannot replace the reached point with a room route"): return
	var ui = main.social_ui
	ui._show_line({"conversation_id": "visit", "game_event_id": "conversation:visit", "sequence": 1,
		"ordered": true, "speech": "npc_dialogue", "participants": [a.npc_name, b.npc_name],
		"speaker": a.npc_name, "text": "一起聊聊", "segments": [{"text": "一起聊聊", "seconds": 1.0}]})
	var next := {"npcs": {a.npc_name: target(floor_id, room, a_position - Vector2(32, 0)),
		b.npc_name: target(floor_id, room, b_position - Vector2(32, 0))}}
	for i in 3:
		await physics_frame
		a._physics_process(1.0 / 60.0)
		b._physics_process(1.0 / 60.0)
	if not check(a.movement_locked() and b.movement_locked() and a.global_position == a_position
			and b.global_position == b_position and not a.is_travelling() and not b.is_travelling(),
			"Both participants stand still during the backend conversation"): return
	ui._tick_playbacks(1.0)
	if not check(not ui.bubble_labels[a.npc_name].visible, "The final bubble finishes before movement is released"): return
	api._apply_world(next)
	await physics_frame
	a._physics_process(1.0 / 60.0)
	b._physics_process(1.0 / 60.0)
	if not check(a.global_position.x < a_position.x and b.global_position.x < b_position.x,
			"Both participants resume their route after backend release"): return
	var walking_route: Array = b._sections
	var stopped_at: Vector2 = b.global_position
	next.npcs[b.npc_name]["encounter_wait"] = "rendezvous"
	api._apply_world(next)
	for i in 12:
		await physics_frame
		a._physics_process(1.0 / 60.0)
		b._physics_process(1.0 / 60.0)
	if not check(b.global_position == stopped_at and a.global_position.x < a_position.x and is_same(walking_route, b._sections),
			"Only the selected waiter freezes, preserving its ongoing route"): return
	if not check(api._pending_positions[b.npc_name].encounter_wait == "rendezvous" and not api._pending_positions[b.npc_name].arrived,
			"Waiting must acknowledge the encounter without falsely completing the previous route"): return
	next.npcs[b.npc_name].erase("encounter_wait")
	api._apply_world(next)
	await physics_frame
	b._physics_process(1.0 / 60.0)
	if not check(b.global_position.x < stopped_at.x and is_same(walking_route, b._sections),
			"Releasing an encounter must resume the previous route"): return

	# A live whisper got stuck here after the NPC's feet drifted onto a furniture tile.
	var floor4: String = game.FLOOR4
	a.reset_to(floor4, "R07")
	a.global_position = game.map_offset(floor4) + Vector2(1093.1, 686.8)
	var blocked_position: Vector2 = a.global_position
	if not check(game.is_blocked(game.cell_of(blocked_position, floor4), floor4), "The saved position must be blocked"): return
	if not check(a.go_to_location(floor_id, "R03") and a.is_travelling(),
			"A whisper should recover a route from an adjacent walkable tile"): return
	if not check(game.room_id_at(a.global_position, floor4) == "R07"
			and a.global_position.distance_to(blocked_position) <= game.tile_size,
			"Recovering a route must keep the NPC in the same room"): return

	api.web_mode = true
	var first := {"position": {"map_id": floor_id, "x": 296.0, "y": 488.0}}
	var second := {"position": {"map_id": floor_id, "x": 344.0, "y": 488.0}}
	a.apply_spectator_position(first)
	a._physics_process(1.0)
	a.apply_spectator_position(second)
	a._physics_process(0.5)
	if not check(a.global_position.is_equal_approx(Vector2(320, 488)),
			"An observer should still be halfway through the movement halfway between snapshots"): return
	a.apply_spectator_position(second)
	a._physics_process(0.5)
	if not check(a.global_position.is_equal_approx(Vector2(344, 488)),
			"A repeated position should not restart interpolation"): return
	a._physics_process(0.1)
	if not check(a.velocity == Vector2.ZERO, "An observer should stop once the latest position is reached"): return

	var zhixing: String = game.ZHIXING
	a.reset_to(floor_id, "R01")
	b.reset_to(zhixing, "zx2_lib")
	main._select_floor(game.MAP_IDS.find(zhixing))
	if not check(main.camera.overview and not a.is_visible_in_tree() and b.is_visible_in_tree(),
			"The Zhixing overview must only show actors on its own map"): return
	a.on_stage = false
	a.on_stage = true
	if not check(not a.is_visible_in_tree(), "Bringing an actor back onstage must respect the viewed floor"): return
	a.reset_to(zhixing, "zx2_lib")
	if not check(a.is_visible_in_tree(), "An actor entering the viewed floor must appear immediately"): return
	a.reset_to(floor_id, "R01")
	main.follow_npc(a)
	main._preview_character(b)
	main._end_status_preview()
	if not check(main.current_map == floor_id and main.camera.target == a and a.is_visible_in_tree(),
			"Ending a cross-floor preview must restore the original camera follow"): return
	print("PASS: movement, spectator interpolation and floor visibility")
	quit(0)
