extends SceneTree

func _initialize() -> void:
	call_deferred("_check")

func press(key: Key, pressed: bool, echo := false) -> void:
	var event := InputEventKey.new()
	event.keycode = key
	event.pressed = pressed
	event.echo = echo
	root.push_input(event)

func _check() -> void:
	var game := root.get_node("Game")
	var api := root.get_node("ApiClient")
	api.set_process(false)
	var scene := load("res://scenes/main.tscn") as PackedScene
	var main := scene.instantiate()
	root.add_child(main)
	current_scene = main
	main.set_physics_process(false)
	main.player_ui.set_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
		actor.social_busy = true
	await process_frame
	assert(main.player_avatar != null)
	assert(not main.player_avatar.visible)
	assert(main.get_node_or_null("MechanicsPanel") != null)
	main.player_ui.active = true
	await process_frame
	assert(main.player_avatar.visible)
	var npc = main.npcs[0]
	npc.social_busy = false
	npc.map_id = game.FLOOR3
	npc.global_position = game.world_of(Vector2i(60, 33))
	main.player_avatar.global_position = npc.global_position + Vector2(96, 0)
	api.backend_online = true
	assert(main._near_npc(npc.npc_name))
	assert(main._nearby_target().npc == npc)
	await process_frame
	await process_frame
	assert(main.interaction_marks.sprites.has(npc.sprite))
	assert(main.interaction_marks.labels.any(func(label: Dictionary) -> bool: return label.text == "按E交互"))
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.player_ui.engage_target == npc.npc_name)
	main.player_ui.conversation_npc = npc.npc_name
	main.player_ui.engage_target = ""
	assert(main.player_ui._near_selected())
	main.player_avatar.global_position += Vector2(1, 0)
	assert(not main._near_npc(npc.npc_name))
	assert(not main.player_ui._near_selected())
	main.player_ui._close_panel()
	assert(not main.player_ui._near_selected())

	api.backend_online = false
	var board: Dictionary = game.floor_data[game.FLOOR3].noticeboards[0]
	main.player_avatar.global_position = game.world_of(Vector2i(29, 27))
	assert(main._nearby_target().is_empty())
	main.player_avatar.global_position = game.world_of(Vector2i(29, 28))
	assert(main._nearby_target().board.id == board.id)
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.notice_ui.visible and main.notice_ui.board_id == board.id)
	main.notice_ui.hide()

	main.player_avatar.global_position = game.world_of(Vector2i(65, 33))
	press(KEY_LEFT, true)
	press(KEY_UP, true)
	press(KEY_LEFT, true, true)
	main._physics_process(0.2)
	assert(game.cell_of(main.player_avatar.global_position) == Vector2i(65, 32))
	press(KEY_UP, false)
	main._physics_process(0.2)
	assert(game.cell_of(main.player_avatar.global_position) == Vector2i(64, 32))
	press(KEY_LEFT, false)

	var entry: Vector2i = game.stair_entry(game.FLOOR3, "stair_west")
	main.player_avatar.global_position = game.world_of(entry + Vector2i.LEFT, game.FLOOR3)
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.current_map == game.FLOOR3)
	press(KEY_RIGHT, true)
	main._physics_process(0.2)
	assert(main.player_avatar.map_id == game.FLOOR4)
	assert(main.current_map == game.FLOOR4)
	main._physics_process(0.2)
	assert(main.current_map == game.FLOOR4)
	press(KEY_RIGHT, false)
	print("player mode smoke OK")
	quit()
